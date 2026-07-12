"""Billing: packages, orders, USDT TRC-20 verify, subscription activation."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BillingSettings, Order, Package, PaymentTxid, Subscription, User

USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


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


def _parse_trc20_tx(data: dict, wallet: str, min_amount: float, contract: str) -> tuple[bool, str, float]:
    if not isinstance(data, dict) or not (data.get("hash") or data.get("contractData")):
        return False, "transaction not found on-chain", 0.0
    ret = str(data.get("contractRet", "SUCCESS")).upper()
    if ret not in ("SUCCESS", ""):
        return False, f"transaction did not succeed ({ret})", 0.0
    confirmed = data.get("confirmed", True)
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
            if not confirmed:
                return False, "payment found but not yet confirmed — try again shortly", amount
            return True, "verified", amount
    return False, "no matching USDT transfer to receiving wallet", 0.0


async def verify_trc20_payment(
    txid: str,
    wallet: str,
    min_amount: float,
    api_base: str,
    api_key: str = "",
    contract: str = USDT_TRC20_CONTRACT,
) -> tuple[bool, str, float]:
    headers = {}
    if api_key:
        headers["TRON-PRO-API-KEY"] = api_key
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{api_base.rstrip('/')}/api/transaction-info",
                params={"hash": txid},
                headers=headers,
            )
            data = r.json()
    except Exception as e:
        return False, f"could not query chain ({e})", 0.0
    return _parse_trc20_tx(data, wallet, min_amount, contract or USDT_TRC20_CONTRACT)


def valid_txid(txid: str) -> bool:
    return bool(txid) and len(txid) >= 40 and bool(re.fullmatch(r"[0-9a-fA-F]+", txid))


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


async def payment_instructions(db: AsyncSession, pkg: Package) -> str:
    b = await get_billing(db)
    lines = [f"Order: {pkg.name} — {pkg.price_usdt} USDT for {pkg.duration_days} days."]
    if b.usdt_enabled and b.usdt_wallet:
        lines.append(
            f"\n💠 USDT (TRC-20):\nSend {pkg.price_usdt} USDT to:\n{b.usdt_wallet}\n"
            f"Then submit TxID via /paid <txid> (Telegram) or panel Subscription page."
        )
    if b.manual_enabled and b.manual_methods:
        lines.append("\n🏦 Manual:")
        for m in b.manual_methods:
            lines.append(f"— {m.get('name')}: {m.get('details')}")
        lines.append("After paying, wait for admin approval.")
    if not (b.usdt_enabled or b.manual_enabled):
        lines.append("\n⚠️ No payment method configured.")
    return "\n".join(lines)
