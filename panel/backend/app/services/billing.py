"""Billing: packages, orders, USDT (TRC-20 / BEP-20), subscription activation."""

from __future__ import annotations

import logging
import re
import secrets
from decimal import Decimal, ROUND_DOWN
from io import BytesIO
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import qrcode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import BillingSettings, Order, Package, PaymentTxid, Subscription, User
from app.services.perms import DEFAULT_USER_PERMS, normalize_perms

log = logging.getLogger("billing")

USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
USDT_BEP20_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"
BSC_CHAIN_ID = 56
MIN_CONFIRMATIONS = 20
# Etherscan API V2 (multichain) — BNB Smart Chain via chainid=56 (replaces BscScan)
ETHERSCAN_V2_API_BASE = "https://api.etherscan.io/v2/api"
BSC_DEFAULT_API_BASE = ETHERSCAN_V2_API_BASE  # alias for callers / settings defaults
BSC_DEFAULT_RPC = "https://bsc-dataseed.binance.org/"
_LEGACY_BSCSCAN_API_BASES = frozenset(
    {
        "https://api.bscscan.com/api",
        "http://api.bscscan.com/api",
        "https://api.bscscan.com/api/",
        "http://api.bscscan.com/api/",
    }
)
# ERC-20 Transfer(address,address,uint256)
_ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def resolve_bep20_api_base(api_base: str | None) -> str:
    """Normalize BEP-20 explorer API base; migrate empty/legacy BscScan defaults to Etherscan V2."""
    raw = (api_base or "").strip()
    if not raw:
        return ETHERSCAN_V2_API_BASE
    normalized = raw.rstrip("/")
    if raw in _LEGACY_BSCSCAN_API_BASES or normalized in {
        b.rstrip("/") for b in _LEGACY_BSCSCAN_API_BASES
    }:
        return ETHERSCAN_V2_API_BASE
    # Any remaining api.bscscan.com host → Etherscan V2 (BscScan API sunset / redirected)
    host = normalized.lower()
    if "://" in host:
        host = host.split("://", 1)[1]
    if host.startswith("api.bscscan.com"):
        return ETHERSCAN_V2_API_BASE
    return raw

# Canonical network keys used in /buy and order.payment_method
NETWORK_TRC20 = "trc20"
NETWORK_BEP20 = "bep20"
METHOD_TRC20 = "usdt_trc20"
METHOD_BEP20 = "usdt_bep20"
METHOD_MANUAL = "manual"

# Persistent Telegram reply-keyboard labels → slash commands
MENU_BUTTON_TO_CMD: dict[str, str] = {
    "Buy": "/buy",
    "Packages": "/packages",
    "Help": "/help",
    "Support": "/support",
    "Run": "/run",
    "Status": "/status",
    # Legacy reply-keyboard labels (no longer shown; keep for stale keyboards).
    "Jobs": "/jobs",
    "Formats": "/formats",
    "Admin": "/admin",
}

_NETWORK_ALIASES = {
    "trc20": NETWORK_TRC20,
    "trc-20": NETWORK_TRC20,
    "tron": NETWORK_TRC20,
    "usdt_trc20": NETWORK_TRC20,
    "bep20": NETWORK_BEP20,
    "bep-20": NETWORK_BEP20,
    "bsc": NETWORK_BEP20,
    "bnb": NETWORK_BEP20,
    "usdt_bep20": NETWORK_BEP20,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_billing(db: AsyncSession) -> BillingSettings:
    row = await db.get(BillingSettings, 1)
    if not row:
        row = BillingSettings(id=1)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def ensure_telegram_user(
    db: AsyncSession,
    telegram_id: str | int,
    *,
    display_name: str | None = None,
) -> User:
    """Create (or return) an enabled panel user linked to this Telegram id."""
    from app.bot.tg_auth import normalize_telegram_id

    tid = normalize_telegram_id(telegram_id, allow_group=False)
    if not tid:
        raise ValueError("telegram_id must be numeric")

    existing = (await db.execute(select(User).where(User.telegram_id == tid))).scalar_one_or_none()
    if existing:
        return existing

    base = (display_name or "").strip()
    # Sanitize display name into a username candidate
    if base:
        slug = re.sub(r"[^a-zA-Z0-9_]+", "_", base)[:24].strip("_").lower()
        username = slug or f"tg_{tid}"
    else:
        username = f"tg_{tid}"
    if (await db.execute(select(User).where(User.username == username))).scalar_one_or_none():
        username = f"tg_{tid}"
    email = f"tg_{tid}@telegram.local"
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        email = f"tg_{tid}_{secrets.token_hex(3)}@telegram.local"

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(secrets.token_urlsafe(24)),
        role="user",
        is_active=True,
        telegram_id=tid,
        perms=normalize_perms({**DEFAULT_USER_PERMS, "telegram_user": True}),
        must_change_password=True,
        totp_enabled=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    log.info("Auto-created telegram buyer user#%s tg=%s", user.id, tid)
    return user


async def active_subscription(db: AsyncSession, user: User) -> Subscription | None:
    if user.role == "admin":
        return None
    sub = (
        await db.execute(
            select(Subscription)
            .where(Subscription.user_id == user.id, Subscription.is_active == True)  # noqa: E712
            .order_by(Subscription.expires_at.desc())
        )
    ).scalars().first()
    if not sub:
        return None
    exp = sub.expires_at if sub.expires_at.tzinfo else sub.expires_at.replace(tzinfo=timezone.utc)
    if exp <= utcnow():
        sub.is_active = False
        await db.commit()
        return None
    return sub


async def user_has_dedicated_worker(db: AsyncSession, user: User) -> bool:
    """True when the user's live subscription package includes dedicated_worker."""
    if user.role == "admin":
        return True
    sub = await active_subscription(db, user)
    if not sub or not sub.package_id:
        return False
    pkg = await db.get(Package, sub.package_id)
    return bool(pkg and getattr(pkg, "dedicated_worker", False))


async def package_for_user(db: AsyncSession, user: User | None) -> Package | None:
    """Active subscription package for a user (None for admin / no sub)."""
    if user is None or user.role == "admin":
        return None
    sub = await active_subscription(db, user)
    if not sub or not sub.package_id:
        return None
    return await db.get(Package, sub.package_id)


async def can_purchase(db: AsyncSession, user: User, pkg: Package) -> tuple[bool, str]:
    if user.role == "admin":
        return False, "Admins do not need a subscription"
    sub = await active_subscription(db, user)
    if sub and int(pkg.tier) < int(sub.tier):
        return False, "Upgrade-only while subscribed — pick the same or a higher tier"
    return True, ""


async def activate_subscription(
    db: AsyncSession,
    user: User,
    pkg: Package,
    *,
    duration_days: int | None = None,
) -> Subscription:
    # deactivate older
    old = (
        await db.execute(
            select(Subscription).where(Subscription.user_id == user.id, Subscription.is_active == True)  # noqa: E712
        )
    ).scalars().all()
    for s in old:
        s.is_active = False
    now = utcnow()
    days = int(duration_days if duration_days is not None else pkg.duration_days)
    sub = Subscription(
        user_id=user.id,
        package_id=pkg.id,
        package_name=pkg.name,
        threads=pkg.threads,
        max_upload_mb=pkg.max_upload_mb,
        tier=pkg.tier,
        starts_at=now,
        expires_at=now + timedelta(days=max(1, days)),
        is_active=True,
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return sub


async def revoke_subscription(db: AsyncSession, sub: Subscription) -> Subscription:
    sub.is_active = False
    await db.commit()
    await db.refresh(sub)
    return sub


async def extend_subscription(db: AsyncSession, sub: Subscription, days: int) -> Subscription:
    now = utcnow()
    exp = sub.expires_at if sub.expires_at.tzinfo else sub.expires_at.replace(tzinfo=timezone.utc)
    base = exp if exp > now else now
    sub.expires_at = base + timedelta(days=max(1, int(days)))
    sub.is_active = True
    await db.commit()
    await db.refresh(sub)
    return sub


async def update_subscription(
    db: AsyncSession,
    sub: Subscription,
    *,
    package: Package | None = None,
    package_name: str | None = None,
    threads: int | None = None,
    max_upload_mb: int | None = None,
    tier: int | None = None,
    expires_at: datetime | None = None,
    is_active: bool | None = None,
) -> Subscription:
    if package is not None:
        sub.package_id = package.id
        sub.package_name = package.name
        if threads is None:
            sub.threads = package.threads
        if max_upload_mb is None:
            sub.max_upload_mb = package.max_upload_mb
        if tier is None:
            sub.tier = package.tier
    if package_name is not None:
        sub.package_name = package_name
    if threads is not None:
        sub.threads = threads
    if max_upload_mb is not None:
        sub.max_upload_mb = max_upload_mb
    if tier is not None:
        sub.tier = tier
    if expires_at is not None:
        sub.expires_at = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    if is_active is not None:
        sub.is_active = is_active
    await db.commit()
    await db.refresh(sub)
    return sub


def subscription_days_left(sub: Subscription) -> float:
    now = utcnow()
    exp = sub.expires_at if sub.expires_at.tzinfo else sub.expires_at.replace(tzinfo=timezone.utc)
    return max(0.0, (exp - now).total_seconds() / 86400)


def subscription_is_live(sub: Subscription) -> bool:
    if not sub.is_active:
        return False
    now = utcnow()
    exp = sub.expires_at if sub.expires_at.tzinfo else sub.expires_at.replace(tzinfo=timezone.utc)
    return exp > now


def normalize_network(raw: str | None) -> str | None:
    if not raw:
        return None
    key = str(raw).strip().lower()
    return _NETWORK_ALIASES.get(key)


def method_for_network(network: str) -> str:
    if network == NETWORK_BEP20:
        return METHOD_BEP20
    if network == NETWORK_TRC20:
        return METHOD_TRC20
    return METHOD_MANUAL


def network_from_method(method: str) -> str | None:
    m = (method or "").strip().lower()
    if m in (METHOD_TRC20, "usdt", "trc20"):
        return NETWORK_TRC20
    if m in (METHOD_BEP20, "bep20"):
        return NETWORK_BEP20
    return None


def available_usdt_networks(b: BillingSettings) -> list[dict]:
    """Enabled USDT networks with wallet + display label."""
    out: list[dict] = []
    if b.usdt_enabled and (b.usdt_wallet or "").strip():
        out.append(
            {
                "key": NETWORK_TRC20,
                "label": "USDT TRC-20 (Tron)",
                "wallet": b.usdt_wallet.strip(),
                "contract": (b.usdt_contract or USDT_TRC20_CONTRACT).strip(),
                "auto_verify": True,
            }
        )
    if getattr(b, "usdt_bep20_enabled", False) and (getattr(b, "usdt_bep20_wallet", "") or "").strip():
        out.append(
            {
                "key": NETWORK_BEP20,
                "label": "USDT BEP-20 (BNB Smart Chain)",
                "wallet": b.usdt_bep20_wallet.strip(),
                "contract": (getattr(b, "usdt_bep20_contract", None) or USDT_BEP20_CONTRACT).strip(),
                "auto_verify": True,
            }
        )
    return out


def resolve_network(b: BillingSettings, requested: str | None) -> tuple[str | None, str]:
    """Pick a network. Returns (network_key, error_message)."""
    nets = available_usdt_networks(b)
    if requested:
        key = normalize_network(requested)
        if not key:
            return None, f"Unknown network '{requested}'. Use trc20 or bep20."
        if not any(n["key"] == key for n in nets):
            return None, f"Network {key} is not enabled."
        return key, ""
    if len(nets) == 1:
        return nets[0]["key"], ""
    if not nets:
        if b.manual_enabled:
            return None, ""  # manual-only
        return None, "No USDT payment network configured."
    return None, "Choose a network (TRC-20 or BEP-20)."


def _as_int(val: Any, default: int = 0) -> int:
    try:
        if val is None or val == "":
            return default
        return int(val)
    except (TypeError, ValueError):
        return default


def _trc20_confirmations(data: dict, latest_block: int | None = None) -> int:
    """Best-effort confirmation count from TronScan transaction-info."""
    for key in ("confirmations", "confirmationsCount", "confirmed_count"):
        if key in data and data[key] is not None:
            n = _as_int(data[key], -1)
            if n >= 0:
                return n
    if data.get("confirmed") is True and latest_block is None:
        # Old APIs only expose a boolean — treat as below threshold so caller must wait
        # unless we can compute from block height.
        pass
    block = _as_int(data.get("block") or data.get("blockNumber"), 0)
    if block > 0 and latest_block is not None and latest_block >= block:
        return max(0, latest_block - block + 1)
    if data.get("confirmed") is True and block <= 0:
        # No block info: refuse auto-grant until we can count confirmations.
        return 0
    return 0


def _parse_trc20_tx(
    data: dict,
    wallet: str,
    min_amount: float,
    contract: str,
    *,
    latest_block: int | None = None,
    min_confirmations: int = MIN_CONFIRMATIONS,
) -> tuple[bool, str, float]:
    if not isinstance(data, dict) or not (data.get("hash") or data.get("contractData")):
        return False, "transaction not found on-chain", 0.0
    ret = str(data.get("contractRet", "SUCCESS")).upper()
    if ret not in ("SUCCESS", ""):
        return False, f"transaction did not succeed ({ret})", 0.0
    transfers = data.get("trc20TransferInfo") or []
    if isinstance(transfers, dict):
        transfers = [transfers]
    for t in transfers:
        to = t.get("to_address") or t.get("to")
        ca = t.get("contract_address") or t.get("contractAddress")
        raw = t.get("amount_str") or t.get("quant") or t.get("amount") or "0"
        if to == wallet and (not contract or ca == contract):
            try:
                amount = int(raw) / 1_000_000.0
            except (TypeError, ValueError):
                amount = 0.0
            if amount + 1e-9 < float(min_amount):
                return False, f"amount {amount:.2f} USDT < required {float(min_amount):.2f}", amount
            conf = _trc20_confirmations(data, latest_block)
            if conf < min_confirmations:
                return (
                    False,
                    f"payment found ({amount:.2f} USDT) but only {conf}/{min_confirmations} confirmations — wait and retry /paid",
                    amount,
                )
            return True, f"verified ({conf} confirmations)", amount
    return False, "no matching USDT transfer to receiving wallet", 0.0


async def verify_trc20_payment(
    txid: str,
    wallet: str,
    min_amount: float,
    api_base: str,
    api_key: str = "",
    contract: str = USDT_TRC20_CONTRACT,
    *,
    min_confirmations: int = MIN_CONFIRMATIONS,
) -> tuple[bool, str, float]:
    headers = {}
    if api_key:
        headers["TRON-PRO-API-KEY"] = api_key
    base = (api_base or "https://apilist.tronscanapi.com").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{base}/api/transaction-info",
                params={"hash": txid},
                headers=headers,
            )
            data = r.json()
            latest_block: int | None = None
            conf_hint = _trc20_confirmations(data if isinstance(data, dict) else {})
            if conf_hint < min_confirmations:
                try:
                    br = await client.get(f"{base}/api/block/latest", headers=headers)
                    bdata = br.json()
                    if isinstance(bdata, dict):
                        latest_block = _as_int(
                            bdata.get("number")
                            or bdata.get("blockHeader", {}).get("raw_data", {}).get("number")
                            or (bdata.get("data") or {}).get("number"),
                            0,
                        ) or None
                except Exception:
                    latest_block = None
    except Exception as e:
        return False, f"could not query chain ({e})", 0.0
    return _parse_trc20_tx(
        data if isinstance(data, dict) else {},
        wallet,
        min_amount,
        contract or USDT_TRC20_CONTRACT,
        latest_block=latest_block,
        min_confirmations=min_confirmations,
    )


def _normalize_evm_txid(txid: str) -> str:
    t = (txid or "").strip()
    if not t.startswith("0x") and not t.startswith("0X"):
        t = "0x" + t
    return t.lower()


def _addr_from_topic(topic: str) -> str:
    t = (topic or "").lower().removeprefix("0x")
    if len(t) < 40:
        return ""
    return "0x" + t[-40:]


def _parse_bep20_receipt(
    receipt: dict,
    wallet: str,
    min_amount: float,
    contract: str,
    *,
    current_block: int,
    min_confirmations: int = MIN_CONFIRMATIONS,
) -> tuple[bool, str, float]:
    if not receipt:
        return False, "transaction not found on-chain", 0.0
    status = receipt.get("status")
    if status in (0, "0", "0x0", "0x00"):
        return False, "transaction failed on-chain", 0.0
    if status not in (1, "1", "0x1", "0x01", None, ""):
        # Unknown status — still try logs if present
        pass
    tx_block = _as_int(receipt.get("blockNumber"), 0)
    if isinstance(receipt.get("blockNumber"), str) and str(receipt.get("blockNumber")).startswith("0x"):
        tx_block = int(receipt["blockNumber"], 16)
    if tx_block <= 0:
        return False, "transaction pending (no block yet) — wait and retry /paid", 0.0
    conf = max(0, current_block - tx_block + 1) if current_block >= tx_block else 0

    wallet_l = wallet.strip().lower()
    contract_l = (contract or USDT_BEP20_CONTRACT).strip().lower()
    amount_found = 0.0
    matched = False
    for lg in receipt.get("logs") or []:
        addr = str(lg.get("address") or "").lower()
        topics = lg.get("topics") or []
        if not topics:
            continue
        topic0 = str(topics[0] or "").lower()
        if topic0 != _ERC20_TRANSFER_TOPIC:
            continue
        if contract_l and addr != contract_l:
            continue
        to_addr = _addr_from_topic(topics[2] if len(topics) > 2 else "")
        if to_addr != wallet_l:
            continue
        raw_hex = str(lg.get("data") or "0x0")
        try:
            raw = int(raw_hex, 16) if raw_hex else 0
        except ValueError:
            raw = 0
        amount = raw / 1e18
        matched = True
        amount_found = max(amount_found, amount)

    if not matched:
        return False, "no matching USDT (BEP-20) transfer to receiving wallet", 0.0
    if amount_found + 1e-9 < float(min_amount):
        return False, f"amount {amount_found:.2f} USDT < required {float(min_amount):.2f}", amount_found
    if conf < min_confirmations:
        return (
            False,
            f"payment found ({amount_found:.2f} USDT) but only {conf}/{min_confirmations} confirmations — wait and retry /paid",
            amount_found,
        )
    return True, f"verified ({conf} confirmations)", amount_found


async def _bsc_rpc(rpc_url: str, method: str, params: list) -> Any:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )
        data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError("invalid RPC response")
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data.get("result")


async def _etherscan_proxy(
    api_base: str,
    api_key: str,
    action: str,
    extra: dict,
    *,
    chain_id: int = BSC_CHAIN_ID,
) -> Any:
    """Call Etherscan-compatible proxy module (V2 multichain uses chainid for BSC=56)."""
    params = {
        "module": "proxy",
        "action": action,
        "chainid": str(chain_id),
        "apikey": api_key or "YourApiKeyToken",
        **extra,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(api_base, params=params)
        data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError("invalid Etherscan response")
    # proxy endpoints return {"jsonrpc","id","result"} or {"status","result","message"}
    if "result" in data and data.get("jsonrpc"):
        return data.get("result")
    if str(data.get("status")) == "0" and data.get("message") not in ("No transactions found",):
        msg = data.get("result") or data.get("message") or "Etherscan error"
        if "api key" in str(msg).lower() or "invalid" in str(msg).lower() and "key" in str(msg).lower():
            raise RuntimeError(str(msg))
        # Some proxy errors still nest result
    return data.get("result")


async def verify_bep20_payment(
    txid: str,
    wallet: str,
    min_amount: float,
    contract: str = USDT_BEP20_CONTRACT,
    *,
    api_base: str = "",
    api_key: str = "",
    rpc_url: str = "",
    min_confirmations: int = MIN_CONFIRMATIONS,
) -> tuple[bool, str, float]:
    """Verify USDT BEP-20 transfer via Etherscan API (chain 56) and/or public BSC RPC (≥ N confirmations)."""
    hx = _normalize_evm_txid(txid)
    if not valid_txid(hx):
        return False, "invalid BEP-20 transaction hash", 0.0

    base = resolve_bep20_api_base(api_base)
    rpc = (rpc_url or BSC_DEFAULT_RPC).strip() or BSC_DEFAULT_RPC
    receipt: dict | None = None
    current_block = 0
    errors: list[str] = []

    if api_key:
        try:
            raw = await _etherscan_proxy(base, api_key, "eth_getTransactionReceipt", {"txhash": hx})
            if isinstance(raw, dict):
                receipt = raw
            raw_bn = await _etherscan_proxy(base, api_key, "eth_blockNumber", {})
            if isinstance(raw_bn, str) and raw_bn.startswith("0x"):
                current_block = int(raw_bn, 16)
            elif raw_bn is not None:
                current_block = _as_int(raw_bn, 0)
        except Exception as e:
            errors.append(f"etherscan: {e}")

    if receipt is None or current_block <= 0:
        try:
            if receipt is None:
                raw = await _bsc_rpc(rpc, "eth_getTransactionReceipt", [hx])
                if isinstance(raw, dict):
                    receipt = raw
            if current_block <= 0:
                raw_bn = await _bsc_rpc(rpc, "eth_blockNumber", [])
                if isinstance(raw_bn, str) and raw_bn.startswith("0x"):
                    current_block = int(raw_bn, 16)
        except Exception as e:
            errors.append(f"rpc: {e}")

    if receipt is None:
        hint = "; ".join(errors) if errors else "no response"
        return False, f"could not query BNB Smart Chain ({hint})", 0.0
    if current_block <= 0:
        return False, "could not read current BSC block height", 0.0

    return _parse_bep20_receipt(
        receipt,
        wallet,
        min_amount,
        contract or USDT_BEP20_CONTRACT,
        current_block=current_block,
        min_confirmations=min_confirmations,
    )


async def verify_usdt_payment(
    network: str,
    txid: str,
    b: BillingSettings,
    min_amount: float,
) -> tuple[bool, str, float]:
    """Dispatch on-chain verify for TRC-20 or BEP-20 with ≥ MIN_CONFIRMATIONS."""
    if network == NETWORK_TRC20:
        return await verify_trc20_payment(
            txid,
            (b.usdt_wallet or "").strip(),
            min_amount,
            b.usdt_api_base,
            b.usdt_api_key or "",
            b.usdt_contract or USDT_TRC20_CONTRACT,
        )
    if network == NETWORK_BEP20:
        return await verify_bep20_payment(
            txid,
            (getattr(b, "usdt_bep20_wallet", "") or "").strip(),
            min_amount,
            getattr(b, "usdt_bep20_contract", None) or USDT_BEP20_CONTRACT,
            api_base=resolve_bep20_api_base(getattr(b, "usdt_bep20_api_base", None)),
            api_key=getattr(b, "usdt_bep20_api_key", None) or "",
            rpc_url=getattr(b, "usdt_bep20_rpc_url", None) or BSC_DEFAULT_RPC,
        )
    return False, f"unsupported network {network}", 0.0


async def fulfill_paid_order(
    db: AsyncSession,
    *,
    user: User,
    order: Order,
    pkg: Package,
    txid: str,
    network: str,
) -> Subscription:
    """Mark order paid, record txid, activate subscription (same outcome as admin approve)."""
    method = method_for_network(network)
    db.add(PaymentTxid(txid=txid, user_id=user.id, order_id=order.id))
    order.status = "paid"
    order.txid = txid
    order.payment_method = method
    # Ensure default panel permissions (same baseline as grant / ensure_telegram_user).
    user.perms = normalize_perms({**(user.perms or {}), **DEFAULT_USER_PERMS, "telegram_user": True})
    await db.commit()
    return await activate_subscription(db, user, pkg)


def valid_txid(txid: str) -> bool:
    """TRON TxID (64 hex) or EVM-style 0x… hash."""
    if not txid:
        return False
    t = txid.strip()
    if t.startswith("0x") or t.startswith("0X"):
        body = t[2:]
        return len(body) == 64 and bool(re.fullmatch(r"[0-9a-fA-F]+", body))
    return len(t) >= 40 and bool(re.fullmatch(r"[0-9a-fA-F]+", t))


async def txid_used(db: AsyncSession, txid: str) -> bool:
    row = (await db.execute(select(PaymentTxid).where(PaymentTxid.txid == txid))).scalar_one_or_none()
    return row is not None


async def create_order(db: AsyncSession, user: User, pkg: Package, method: str = "") -> Order:
    # cancel other pending for user
    pending = (
        await db.execute(
            select(Order).where(Order.user_id == user.id, Order.status == "pending")
        )
    ).scalars().all()
    for o in pending:
        o.status = "cancelled"
    order = Order(
        user_id=user.id,
        package_id=pkg.id,
        status="pending",
        payment_method=method,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order


def bep20_payment_uri(wallet: str, amount_usdt: float, contract: str = USDT_BEP20_CONTRACT) -> str:
    """EIP-681 URI for ERC-20 transfer on BSC (chain 56)."""
    units = int(
        (Decimal(str(amount_usdt)).quantize(Decimal("0.000001"), rounding=ROUND_DOWN) * Decimal(10**18))
    )
    ca = (contract or USDT_BEP20_CONTRACT).strip()
    return f"ethereum:{ca}@{BSC_CHAIN_ID}/transfer?address={wallet.strip()}&uint256={units}"


def qr_png_bytes(payload: str) -> bytes:
    img = qrcode.make(payload)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def payment_qr_payload(network: str, wallet: str, amount: float, contract: str = "") -> str:
    """Best-effort QR contents: EIP-681 for BEP20, raw address for TRC20."""
    if network == NETWORK_BEP20:
        return bep20_payment_uri(wallet, amount, contract or USDT_BEP20_CONTRACT)
    return wallet.strip()


async def payment_instructions(
    db: AsyncSession,
    pkg: Package,
    *,
    order: Order | None = None,
    network: str | None = None,
) -> tuple[str, bytes | None]:
    """Return (caption text, optional QR PNG bytes)."""
    b = await get_billing(db)
    order_id = order.id if order else None
    lines = [
        f"Order #{order_id or '—'}: {pkg.name}",
        f"Amount: {pkg.price_usdt} USDT",
        f"Duration: {pkg.duration_days} days · threads {pkg.threads}",
    ]
    qr: bytes | None = None
    net = network or (network_from_method(order.payment_method) if order else None)

    if net == NETWORK_TRC20 and b.usdt_enabled and b.usdt_wallet:
        wallet = b.usdt_wallet.strip()
        lines.extend(
            [
                "",
                "💠 Network: USDT TRC-20 (Tron)",
                f"Send exactly {pkg.price_usdt} USDT to:",
                wallet,
                "",
                "After paying, wait for ≥20 confirmations, then submit TxID:",
                f"/paid <txid>",
            ]
        )
        if order_id:
            lines.append(f"(Order id: {order_id} — include in memo if your wallet supports it)")
        try:
            qr = qr_png_bytes(payment_qr_payload(NETWORK_TRC20, wallet, float(pkg.price_usdt)))
        except Exception:
            log.exception("QR generate failed (trc20)")
    elif net == NETWORK_BEP20 and getattr(b, "usdt_bep20_enabled", False) and getattr(b, "usdt_bep20_wallet", ""):
        wallet = b.usdt_bep20_wallet.strip()
        contract = (getattr(b, "usdt_bep20_contract", None) or USDT_BEP20_CONTRACT).strip()
        lines.extend(
            [
                "",
                "💠 Network: USDT BEP-20 (BNB Smart Chain)",
                f"Send exactly {pkg.price_usdt} USDT to:",
                wallet,
                f"Token contract: {contract}",
                "",
                "After paying, wait for ≥20 confirmations, then submit TxID:",
                f"/paid <txid>",
            ]
        )
        if order_id:
            lines.append(f"Order id: {order_id}")
        try:
            qr = qr_png_bytes(payment_qr_payload(NETWORK_BEP20, wallet, float(pkg.price_usdt), contract))
        except Exception:
            log.exception("QR generate failed (bep20)")
    elif not net:
        # Overview of all available methods (pre-network selection or listing)
        nets = available_usdt_networks(b)
        if nets:
            lines.append("")
            for n in nets:
                lines.append(f"💠 {n['label']}:")
                lines.append(f"  Send {pkg.price_usdt} USDT to {n['wallet']}")
            lines.append("")
            lines.append("Step order: pick a package → choose network → pay → /paid <txid>")
        if b.manual_enabled and b.manual_methods:
            lines.append("\n🏦 Manual:")
            for m in b.manual_methods:
                lines.append(f"— {m.get('name')}: {m.get('details')}")
            lines.append("After paying, wait for admin approval.")
        if not nets and not (b.manual_enabled and b.manual_methods):
            lines.append("\n⚠️ No payment method configured.")
        return "\n".join(lines), None
    else:
        if b.manual_enabled and b.manual_methods:
            lines.append("\n🏦 Manual:")
            for m in b.manual_methods:
                lines.append(f"— {m.get('name')}: {m.get('details')}")
            lines.append("After paying, wait for admin approval.")
        else:
            lines.append("\n⚠️ Selected network is not available.")

    if b.manual_enabled and b.manual_methods and net:
        lines.append("\nOr pay manually:")
        for m in b.manual_methods:
            lines.append(f"— {m.get('name')}: {m.get('details')}")
        lines.append("Admin will approve after confirmation.")

    return "\n".join(lines), qr


def packages_inline_keyboard(pkgs: list[Package]) -> dict:
    """Telegram inline keyboard: one button per package."""
    rows: list[list[dict]] = []
    for p in sorted(pkgs, key=lambda x: x.tier):
        label = f"{p.name} — {p.price_usdt} USDT / {p.duration_days}d"
        if len(label) > 64:
            label = f"{p.slug} — {p.price_usdt} USDT"
        rows.append([{"text": label, "callback_data": f"buy:{p.slug}"}])
    return {"inline_keyboard": rows}


def network_inline_keyboard(slug: str, networks: list[dict]) -> dict:
    rows = [
        [{"text": n["label"], "callback_data": f"buynet:{slug}:{n['key']}"}]
        for n in networks
    ]
    return {"inline_keyboard": rows}


def user_reply_keyboard(
    *,
    is_admin: bool = False,
    has_sub: bool = False,
    support_enabled: bool = True,
) -> dict:
    """Persistent Telegram reply keyboard (always-on chrome).

    Status covers job progress (typed /jobs still works). Help includes upload
    formats (typed /formats still works). Jobs/Formats are not shown as buttons.
    """
    if is_admin:
        rows: list[list[dict]] = [
            [{"text": "Run"}, {"text": "Status"}, {"text": "Buy"}, {"text": "Packages"}],
            [{"text": "Admin"}, {"text": "Help"}],
        ]
        if support_enabled:
            rows[1].insert(1, {"text": "Support"})
    elif has_sub:
        rows = [
            [{"text": "Run"}, {"text": "Status"}, {"text": "Buy"}],
            [{"text": "Help"}],
        ]
        if support_enabled:
            rows[1].insert(0, {"text": "Support"})
    else:
        rows = [
            [{"text": "Buy"}, {"text": "Packages"}],
            [{"text": "Help"}],
        ]
        if support_enabled:
            rows[1].append({"text": "Support"})
    return {
        "keyboard": rows,
        "resize_keyboard": True,
        "is_persistent": True,
    }


def resolve_menu_text(text: str) -> str | None:
    """Map reply-keyboard label to a slash command, or None."""
    key = (text or "").strip()
    return MENU_BUTTON_TO_CMD.get(key)


def format_packages_list(pkgs: list[Package]) -> str:
    lines = ["Available packages:"]
    for p in sorted(pkgs, key=lambda x: x.tier):
        lines.append(
            f"• {p.name} ({p.slug}) — {p.price_usdt} USDT / {p.duration_days}d | "
            f"threads {p.threads}, upload {p.max_upload_mb}MB"
        )
    lines.append("\nTap a package below, or: /buy <slug>")
    lines.append("Then choose TRC-20 or BEP-20 → pay → /paid <txid>")
    return "\n".join(lines)
