from datetime import datetime, timedelta, timezone
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DEFAULT_USER_PERMS, require_admin, require_ready_user
from app.core.database import get_db
from app.core.security import hash_password
from app.models import AuditLog, BillingSettings, Order, Package, PaymentTxid, Subscription, User
from app.services.perms import DEFAULT_USER_PERMS, normalize_perms
from app.api.users import _set_worker_ids, _worker_ids_for
from app.schemas import (
    BillingSettingsOut,
    BillingSettingsUpdate,
    GrantRequest,
    PackageCreate,
    PackageOut,
    PackageUpdate,
    SubscriberOut,
    SubscriptionAdminOut,
    SubscriptionExtend,
    SubscriptionOut,
    SubscriptionUpdate,
    TelegramUserCreate,
    TelegramUserUpdate,
)
from app.services import billing as billing_svc

router = APIRouter(tags=["billing"])


class BuyRequest(BaseModel):
    package_slug: str
    network: str | None = None  # trc20 | bep20


class PaidRequest(BaseModel):
    txid: str


class ApproveRequest(BaseModel):
    order_id: int


class RejectRequest(BaseModel):
    order_id: int
    reason: str = ""


class OrderOut(BaseModel):
    id: int
    user_id: int
    package_id: int
    status: str
    payment_method: str
    txid: str | None
    created_at: datetime
    package_name: str = ""
    username: str = ""
    telegram_id: str | None = None


async def _billing(db: AsyncSession) -> BillingSettings:
    return await billing_svc.get_billing(db)


def _sub_out(sub: Subscription) -> SubscriptionOut:
    days = billing_svc.subscription_days_left(sub)
    return SubscriptionOut(
        id=sub.id,
        package_name=sub.package_name,
        threads=sub.threads,
        max_upload_mb=sub.max_upload_mb,
        tier=sub.tier,
        starts_at=sub.starts_at,
        expires_at=sub.expires_at,
        is_active=billing_svc.subscription_is_live(sub),
        days_left=days,
    )


def _sub_admin_out(sub: Subscription, user: User | None = None) -> SubscriptionAdminOut:
    return SubscriptionAdminOut(
        id=sub.id,
        user_id=sub.user_id,
        username=user.username if user else "",
        telegram_id=user.telegram_id if user else None,
        package_id=sub.package_id,
        package_name=sub.package_name,
        threads=sub.threads,
        max_upload_mb=sub.max_upload_mb,
        tier=sub.tier,
        starts_at=sub.starts_at,
        expires_at=sub.expires_at,
        is_active=billing_svc.subscription_is_live(sub),
        days_left=billing_svc.subscription_days_left(sub),
        user_is_active=bool(user.is_active) if user else True,
    )


async def _resolve_grant_user(db: AsyncSession, body: GrantRequest) -> User:
    user: User | None = None
    if body.user_id is not None:
        user = await db.get(User, body.user_id)
    elif body.telegram_id:
        tid = str(body.telegram_id).strip()
        user = (await db.execute(select(User).where(User.telegram_id == tid))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found (provide user_id or telegram_id)")
    return user


# --- packages ---


@router.get("/packages", response_model=list[PackageOut])
async def list_packages(user: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    from app.services.scrape_profiles import migrate_packages_from_profiles
    from app.services.worker_config import package_defaults_from_package

    await migrate_packages_from_profiles(db)
    q = select(Package).order_by(Package.tier)
    if user.role != "admin":
        q = q.where(Package.is_active == True)  # noqa: E712
    rows = (await db.execute(q)).scalars().all()
    # Ensure API always returns filled scrape_defaults
    out = []
    for pkg in rows:
        if not pkg.scrape_defaults:
            pkg.scrape_defaults = package_defaults_from_package(pkg)
        out.append(pkg)
    return out


@router.post("/packages", response_model=PackageOut)
async def create_package(
    body: PackageCreate,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.worker_config import build_package_scrape_defaults, normalize_worker_config

    if (await db.execute(select(Package).where(Package.slug == body.slug))).scalar_one_or_none():
        raise HTTPException(400, "Slug exists")
    data = body.model_dump()
    scrape_defaults = data.pop("scrape_defaults", None)
    pkg = Package(**data)
    if scrape_defaults:
        pkg.scrape_defaults = normalize_worker_config(
            {**scrape_defaults, "threads": pkg.threads}
        )
    else:
        pkg.scrape_defaults = build_package_scrape_defaults(threads=pkg.threads)
    if not pkg.chunk_size:
        pkg.chunk_size = 500
    db.add(pkg)
    await db.commit()
    await db.refresh(pkg)
    return pkg


@router.patch("/packages/{package_id}", response_model=PackageOut)
async def update_package(
    package_id: int,
    body: PackageUpdate,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.worker_config import normalize_worker_config

    pkg = await db.get(Package, package_id)
    if not pkg:
        raise HTTPException(404, "Not found")
    data = body.model_dump(exclude_unset=True)
    scrape_defaults = data.pop("scrape_defaults", None)
    for k, v in data.items():
        setattr(pkg, k, v)
    if scrape_defaults is not None:
        pkg.scrape_defaults = normalize_worker_config(
            {**scrape_defaults, "threads": pkg.threads}
        )
    elif body.threads is not None:
        # Keep embedded scrape defaults' threads aligned with package allowance
        cfg = dict(pkg.scrape_defaults or {})
        cfg["threads"] = pkg.threads
        pkg.scrape_defaults = normalize_worker_config(cfg)
    await db.commit()
    await db.refresh(pkg)
    return pkg


@router.delete("/packages/{package_id}")
async def delete_package(
    package_id: int,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    pkg = await db.get(Package, package_id)
    if not pkg:
        raise HTTPException(404, "Not found")
    pkg.is_active = False
    await db.commit()
    return {"detail": "Disabled"}


# --- billing settings ---


@router.get("/billing/settings", response_model=BillingSettingsOut)
async def get_billing_settings(
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    b = await _billing(db)
    return BillingSettingsOut(
        enabled=b.enabled,
        usdt_enabled=b.usdt_enabled,
        usdt_wallet=b.usdt_wallet,
        usdt_contract=b.usdt_contract,
        usdt_api_base=b.usdt_api_base,
        usdt_api_key_configured=bool(b.usdt_api_key),
        usdt_bep20_enabled=bool(getattr(b, "usdt_bep20_enabled", False)),
        usdt_bep20_wallet=getattr(b, "usdt_bep20_wallet", "") or "",
        usdt_bep20_contract=getattr(b, "usdt_bep20_contract", None)
        or "0x55d398326f99059fF775485246999027B3197955",
        usdt_bep20_api_base=billing_svc.resolve_bep20_api_base(getattr(b, "usdt_bep20_api_base", None)),
        usdt_bep20_api_key_configured=bool(getattr(b, "usdt_bep20_api_key", "") or ""),
        usdt_bep20_rpc_url=getattr(b, "usdt_bep20_rpc_url", None) or "https://bsc-dataseed.binance.org/",
        manual_enabled=b.manual_enabled,
        manual_methods=b.manual_methods or [],
        allowed_extensions=b.allowed_extensions or [],
        max_upload_mb=b.max_upload_mb,
    )


@router.get("/billing/public")
async def billing_public(user: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    b = await _billing(db)
    return {
        "enabled": b.enabled,
        "usdt_enabled": b.usdt_enabled,
        "usdt_wallet": b.usdt_wallet if b.usdt_enabled else "",
        "usdt_bep20_enabled": bool(getattr(b, "usdt_bep20_enabled", False)),
        "usdt_bep20_wallet": (getattr(b, "usdt_bep20_wallet", "") or "")
        if getattr(b, "usdt_bep20_enabled", False)
        else "",
        "networks": billing_svc.available_usdt_networks(b),
        "manual_enabled": b.manual_enabled,
        "manual_methods": b.manual_methods if b.manual_enabled else [],
    }


@router.put("/billing/settings", response_model=BillingSettingsOut)
async def update_billing_settings(
    body: BillingSettingsUpdate,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    b = await _billing(db)
    data = body.model_dump(exclude_unset=True)
    if "usdt_bep20_api_base" in data:
        data["usdt_bep20_api_base"] = billing_svc.resolve_bep20_api_base(data.get("usdt_bep20_api_base"))
    for k, v in data.items():
        setattr(b, k, v)
    await db.commit()
    return await get_billing_settings(_, __, db)


# --- telegram subscribers ---


def _subscriber_out(
    u: User,
    sub_out,
    live: bool,
    worker_ids: list[int] | None = None,
    dedicated_worker: bool = False,
) -> SubscriberOut:
    return SubscriberOut(
        user_id=u.id,
        username=u.username,
        email=str(u.email),
        role=u.role,
        is_active=u.is_active,
        telegram_id=u.telegram_id,
        totp_enabled=u.totp_enabled,
        created_at=u.created_at,
        subscription=sub_out,
        has_active_subscription=live,
        perms=normalize_perms(u.perms),
        worker_ids=worker_ids or [],
        dedicated_worker=dedicated_worker,
    )


@router.get("/billing/subscribers", response_model=list[SubscriberOut])
async def list_subscribers(
    telegram_only: bool = False,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(User).order_by(User.id)
    if telegram_only:
        q = q.where(User.telegram_id.is_not(None))
    users = (await db.execute(q)).scalars().all()
    out: list[SubscriberOut] = []
    for u in users:
        sub = await billing_svc.active_subscription(db, u)
        if not sub:
            last = (
                await db.execute(
                    select(Subscription)
                    .where(Subscription.user_id == u.id)
                    .order_by(Subscription.expires_at.desc())
                )
            ).scalars().first()
            sub_out = _sub_admin_out(last, u) if last else None
            live = False
        else:
            sub_out = _sub_admin_out(sub, u)
            live = True
        out.append(
            _subscriber_out(
                u,
                sub_out,
                live,
                await _worker_ids_for(db, u.id),
                await billing_svc.user_has_dedicated_worker(db, u),
            )
        )
    return out


@router.post("/billing/telegram-users", response_model=SubscriberOut)
async def create_telegram_user(
    body: TelegramUserCreate,
    admin: User = Depends(require_admin),
    _: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    tid = str(body.telegram_id).strip()
    if not tid.isdigit():
        raise HTTPException(400, "telegram_id must be a numeric Telegram user id")
    existing = (await db.execute(select(User).where(User.telegram_id == tid))).scalar_one_or_none()
    if existing:
        raise HTTPException(400, f"Telegram id already linked to {existing.username}")

    username = (body.username or f"tg_{tid}").strip()
    if (await db.execute(select(User).where(User.username == username))).scalar_one_or_none():
        raise HTTPException(400, "Username already exists")

    email = (body.email or f"tg_{tid}@telegram.local").strip().lower()
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise HTTPException(400, "Email already exists")

    password = body.password or secrets.token_urlsafe(12)
    base_perms = {**DEFAULT_USER_PERMS, "telegram_user": True}
    if body.perms:
        base_perms.update(body.perms)
        base_perms["telegram_user"] = True
    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        role="user",
        telegram_id=tid,
        is_active=body.is_active,
        perms=normalize_perms(base_perms),
        must_change_password=True,
        totp_enabled=False,
    )
    db.add(user)
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="telegram_user.create",
            detail={"telegram_id": tid, "username": username},
        )
    )
    await db.commit()
    await db.refresh(user)

    if body.worker_ids is not None:
        await _set_worker_ids(db, user.id, body.worker_ids)
        await db.commit()

    sub_out = None
    live = False
    if body.package_id:
        pkg = await db.get(Package, body.package_id)
        if not pkg:
            raise HTTPException(404, "Package not found")
        sub = await billing_svc.activate_subscription(
            db, user, pkg, duration_days=body.duration_days
        )
        sub_out = _sub_admin_out(sub, user)
        live = True
        if body.notify:
            from app.services.notify import notify_user_telegram

            await notify_user_telegram(
                db,
                user,
                f"✅ Access granted: {pkg.name} until {sub.expires_at.date()}.",
            )

    return _subscriber_out(
        user,
        sub_out,
        live,
        await _worker_ids_for(db, user.id),
        await billing_svc.user_has_dedicated_worker(db, user),
    )


@router.patch("/billing/telegram-users/{user_id}", response_model=SubscriberOut)
async def update_telegram_user(
    user_id: int,
    body: TelegramUserUpdate,
    admin: User = Depends(require_admin),
    _: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    data = body.model_dump(exclude_unset=True)
    unlink = data.pop("unlink_telegram", False)
    reset_2fa = data.pop("reset_2fa", False)
    password = data.pop("password", None)
    perms_patch = data.pop("perms", None)
    worker_ids = data.pop("worker_ids", None)

    if unlink:
        user.telegram_id = None
    elif "telegram_id" in data:
        tid = str(data["telegram_id"]).strip() if data["telegram_id"] else None
        if tid:
            if not tid.isdigit():
                raise HTTPException(400, "telegram_id must be numeric")
            clash = (
                await db.execute(
                    select(User).where(User.telegram_id == tid, User.id != user.id)
                )
            ).scalar_one_or_none()
            if clash:
                raise HTTPException(400, f"Telegram id already linked to {clash.username}")
            user.telegram_id = tid
        else:
            user.telegram_id = None

    if "username" in data and data["username"]:
        clash = (
            await db.execute(
                select(User).where(User.username == data["username"], User.id != user.id)
            )
        ).scalar_one_or_none()
        if clash:
            raise HTTPException(400, "Username already exists")
        user.username = data["username"]

    if "email" in data and data["email"] is not None:
        email = str(data["email"]).strip().lower()
        clash = (
            await db.execute(select(User).where(User.email == email, User.id != user.id))
        ).scalar_one_or_none()
        if clash:
            raise HTTPException(400, "Email already exists")
        user.email = email

    if "is_active" in data and data["is_active"] is not None:
        user.is_active = bool(data["is_active"])

    if password:
        user.password_hash = hash_password(password)
        user.must_change_password = True

    if reset_2fa:
        user.totp_secret = None
        user.totp_enabled = False

    if perms_patch is not None:
        merged = {**(user.perms or {}), **perms_patch}
        if user.perms and user.perms.get("telegram_user"):
            merged["telegram_user"] = True
        user.perms = normalize_perms(merged)

    if worker_ids is not None:
        await _set_worker_ids(db, user.id, worker_ids)

    db.add(AuditLog(actor_id=admin.id, action="telegram_user.update", detail={"user_id": user_id}))
    await db.commit()
    await db.refresh(user)

    sub = await billing_svc.active_subscription(db, user)
    return _subscriber_out(
        user,
        _sub_admin_out(sub, user) if sub else None,
        bool(sub),
        await _worker_ids_for(db, user.id),
        await billing_svc.user_has_dedicated_worker(db, user),
    )


@router.delete("/billing/telegram-users/{user_id}")
async def delete_telegram_user(
    user_id: int,
    unlink_only: bool = False,
    admin: User = Depends(require_admin),
    _: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(400, "Cannot delete yourself")
    if unlink_only:
        user.telegram_id = None
        db.add(AuditLog(actor_id=admin.id, action="telegram_user.unlink", detail={"user_id": user_id}))
        await db.commit()
        return {"detail": "Telegram unlinked"}
    # revoke subs then delete
    subs = (
        await db.execute(select(Subscription).where(Subscription.user_id == user.id))
    ).scalars().all()
    for s in subs:
        s.is_active = False
    await db.delete(user)
    db.add(AuditLog(actor_id=admin.id, action="telegram_user.delete", detail={"user_id": user_id}))
    await db.commit()
    return {"detail": "Deleted"}


# --- subscriptions ---


@router.get("/subscriptions/me", response_model=SubscriptionOut | None)
async def my_subscription(user: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    if user.role == "admin":
        now = datetime.now(timezone.utc)
        return SubscriptionOut(
            id=0,
            package_name="Admin",
            threads=999,
            max_upload_mb=999,
            tier=999,
            starts_at=now,
            expires_at=now + timedelta(days=3650),
            is_active=True,
            days_left=3650,
        )
    sub = await billing_svc.active_subscription(db, user)
    return _sub_out(sub) if sub else None


@router.get("/subscriptions", response_model=list[SubscriptionAdminOut])
async def list_subscriptions(
    active_only: bool = False,
    user_id: int | None = None,
    telegram_id: str | None = None,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Subscription).order_by(Subscription.id.desc())
    if user_id is not None:
        q = q.where(Subscription.user_id == user_id)
    if telegram_id:
        u = (
            await db.execute(select(User).where(User.telegram_id == str(telegram_id).strip()))
        ).scalar_one_or_none()
        if not u:
            return []
        q = q.where(Subscription.user_id == u.id)
    rows = (await db.execute(q)).scalars().all()
    out: list[SubscriptionAdminOut] = []
    for sub in rows:
        live = billing_svc.subscription_is_live(sub)
        if active_only and not live:
            continue
        user = await db.get(User, sub.user_id)
        out.append(_sub_admin_out(sub, user))
    return out


@router.post("/subscriptions/grant")
async def grant_subscription(
    body: GrantRequest,
    admin: User = Depends(require_admin),
    _: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    if body.user_id is None and not body.telegram_id:
        raise HTTPException(400, "Provide user_id or telegram_id")
    user = await _resolve_grant_user(db, body)
    pkg = await db.get(Package, body.package_id)
    if not pkg:
        raise HTTPException(404, "Package not found")
    sub = await billing_svc.activate_subscription(
        db, user, pkg, duration_days=body.duration_days
    )
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="subscription.grant",
            detail={"user_id": user.id, "package_id": pkg.id, "subscription_id": sub.id},
        )
    )
    await db.commit()
    if body.notify:
        from app.services.notify import notify_user_telegram

        await notify_user_telegram(
            db,
            user,
            f"✅ Subscription granted: {pkg.name} until {sub.expires_at.date()}.",
        )
    return {
        "detail": "Granted",
        "expires_at": sub.expires_at.isoformat(),
        "subscription": _sub_admin_out(sub, user),
    }


@router.patch("/subscriptions/{subscription_id}", response_model=SubscriptionAdminOut)
async def update_subscription(
    subscription_id: int,
    body: SubscriptionUpdate,
    admin: User = Depends(require_admin),
    _: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    sub = await db.get(Subscription, subscription_id)
    if not sub:
        raise HTTPException(404, "Subscription not found")
    data = body.model_dump(exclude_unset=True)
    pkg = None
    if "package_id" in data:
        pid = data.pop("package_id")
        if pid is not None:
            pkg = await db.get(Package, pid)
            if not pkg:
                raise HTTPException(404, "Package not found")
    sub = await billing_svc.update_subscription(db, sub, package=pkg, **data)
    # if activating this one, deactivate siblings
    if sub.is_active and billing_svc.subscription_is_live(sub):
        siblings = (
            await db.execute(
                select(Subscription).where(
                    Subscription.user_id == sub.user_id,
                    Subscription.id != sub.id,
                    Subscription.is_active == True,  # noqa: E712
                )
            )
        ).scalars().all()
        for s in siblings:
            s.is_active = False
        await db.commit()
        await db.refresh(sub)
    user = await db.get(User, sub.user_id)
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="subscription.update",
            detail={"subscription_id": subscription_id},
        )
    )
    await db.commit()
    return _sub_admin_out(sub, user)


@router.post("/subscriptions/{subscription_id}/extend", response_model=SubscriptionAdminOut)
async def extend_subscription(
    subscription_id: int,
    body: SubscriptionExtend,
    admin: User = Depends(require_admin),
    _: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    sub = await db.get(Subscription, subscription_id)
    if not sub:
        raise HTTPException(404, "Subscription not found")
    sub = await billing_svc.extend_subscription(db, sub, body.days)
    user = await db.get(User, sub.user_id)
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="subscription.extend",
            detail={"subscription_id": subscription_id, "days": body.days},
        )
    )
    await db.commit()
    return _sub_admin_out(sub, user)


@router.post("/subscriptions/{subscription_id}/revoke", response_model=SubscriptionAdminOut)
async def revoke_subscription(
    subscription_id: int,
    admin: User = Depends(require_admin),
    _: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    sub = await db.get(Subscription, subscription_id)
    if not sub:
        raise HTTPException(404, "Subscription not found")
    sub = await billing_svc.revoke_subscription(db, sub)
    user = await db.get(User, sub.user_id)
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="subscription.revoke",
            detail={"subscription_id": subscription_id},
        )
    )
    await db.commit()
    if user:
        from app.services.notify import notify_user_telegram

        await notify_user_telegram(db, user, "⚠️ Your subscription was revoked by an admin.")
    return _sub_admin_out(sub, user)


# --- orders ---


@router.post("/orders/buy")
async def buy_package(
    body: BuyRequest,
    user: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    b = await _billing(db)
    if not b.enabled:
        raise HTTPException(400, "Billing is disabled")
    pkg = (
        await db.execute(select(Package).where(Package.slug == body.package_slug, Package.is_active == True))  # noqa: E712
    ).scalar_one_or_none()
    if not pkg:
        raise HTTPException(404, "Package not found")
    ok, why = await billing_svc.can_purchase(db, user, pkg)
    if not ok:
        raise HTTPException(400, why)
    nets = billing_svc.available_usdt_networks(b)
    network, net_err = billing_svc.resolve_network(b, body.network)
    if body.network and net_err:
        raise HTTPException(400, net_err)
    if network:
        method = billing_svc.method_for_network(network)
    elif b.manual_enabled:
        method = billing_svc.METHOD_MANUAL
    elif nets:
        raise HTTPException(400, net_err or "Choose a network: trc20 or bep20")
    else:
        method = ""
    order = await billing_svc.create_order(db, user, pkg, method)
    instructions, _qr = await billing_svc.payment_instructions(db, pkg, order=order, network=network)
    return {
        "order_id": order.id,
        "network": network,
        "payment_method": method,
        "instructions": instructions,
        "package": PackageOut.model_validate(pkg),
    }


@router.post("/orders/paid")
async def submit_paid(
    body: PaidRequest,
    user: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    b = await _billing(db)
    if not billing_svc.valid_txid(body.txid):
        raise HTTPException(400, "Invalid transaction id")
    if await billing_svc.txid_used(db, body.txid):
        raise HTTPException(400, "Transaction already used")

    order = (
        await db.execute(
            select(Order).where(Order.user_id == user.id, Order.status == "pending").order_by(Order.id.desc())
        )
    ).scalars().first()
    if not order:
        raise HTTPException(400, "No pending order — buy a package first")
    net = billing_svc.network_from_method(order.payment_method)
    if net not in (billing_svc.NETWORK_TRC20, billing_svc.NETWORK_BEP20):
        raise HTTPException(400, "On-chain verify is only for TRC-20 / BEP-20 orders")
    if net == billing_svc.NETWORK_TRC20 and (not b.usdt_enabled or not b.usdt_wallet):
        raise HTTPException(400, "USDT TRC-20 payments not enabled")
    if net == billing_svc.NETWORK_BEP20 and (
        not getattr(b, "usdt_bep20_enabled", False) or not (getattr(b, "usdt_bep20_wallet", "") or "").strip()
    ):
        raise HTTPException(400, "USDT BEP-20 payments not enabled")
    pkg = await db.get(Package, order.package_id)
    if not pkg:
        raise HTTPException(400, "Package missing")

    ok, detail, amount = await billing_svc.verify_usdt_payment(net, body.txid, b, float(pkg.price_usdt))
    if not ok:
        raise HTTPException(400, detail)

    sub = await billing_svc.fulfill_paid_order(
        db, user=user, order=order, pkg=pkg, txid=body.txid.strip(), network=net
    )
    return {
        "detail": f"Payment verified ({amount:.2f} USDT) — {detail}",
        "subscription": _sub_out(sub),
    }


@router.get("/orders/pending", response_model=list[OrderOut])
async def pending_orders(
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(select(Order).where(Order.status == "pending").order_by(Order.id.desc()))
    ).scalars().all()
    out = []
    for o in rows:
        pkg = await db.get(Package, o.package_id)
        u = await db.get(User, o.user_id)
        out.append(
            OrderOut(
                id=o.id,
                user_id=o.user_id,
                package_id=o.package_id,
                status=o.status,
                payment_method=o.payment_method,
                txid=o.txid,
                created_at=o.created_at,
                package_name=pkg.name if pkg else "",
                username=u.username if u else "",
                telegram_id=u.telegram_id if u else None,
            )
        )
    return out


@router.get("/orders", response_model=list[OrderOut])
async def list_orders(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Order).order_by(Order.id.desc()).limit(limit)
    if status_filter:
        q = q.where(Order.status == status_filter)
    rows = (await db.execute(q)).scalars().all()
    out = []
    for o in rows:
        pkg = await db.get(Package, o.package_id)
        u = await db.get(User, o.user_id)
        out.append(
            OrderOut(
                id=o.id,
                user_id=o.user_id,
                package_id=o.package_id,
                status=o.status,
                payment_method=o.payment_method,
                txid=o.txid,
                created_at=o.created_at,
                package_name=pkg.name if pkg else "",
                username=u.username if u else "",
                telegram_id=u.telegram_id if u else None,
            )
        )
    return out


@router.post("/orders/approve")
async def approve_order(
    body: ApproveRequest,
    admin: User = Depends(require_admin),
    _: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    order = await db.get(Order, body.order_id)
    if not order or order.status != "pending":
        raise HTTPException(404, "Pending order not found")
    user = await db.get(User, order.user_id)
    pkg = await db.get(Package, order.package_id)
    if not user or not pkg:
        raise HTTPException(404, "User/package missing")
    order.status = "approved"
    order.payment_method = order.payment_method or "manual"
    user.perms = normalize_perms({**(user.perms or {}), **DEFAULT_USER_PERMS, "telegram_user": True})
    await db.commit()
    sub = await billing_svc.activate_subscription(db, user, pkg)
    from app.services.notify import notify_user_telegram

    await notify_user_telegram(
        db,
        user,
        f"✅ Your {pkg.name} subscription is active until {sub.expires_at.date()}.",
        reply_markup=billing_svc.user_reply_keyboard(
            is_admin=user.role == "admin",
            has_sub=True,
        ),
    )
    return {"detail": "Approved", "expires_at": sub.expires_at.isoformat()}


@router.post("/orders/reject")
async def reject_order(
    body: RejectRequest,
    admin: User = Depends(require_admin),
    _: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    order = await db.get(Order, body.order_id)
    if not order or order.status != "pending":
        raise HTTPException(404, "Pending order not found")
    user = await db.get(User, order.user_id)
    order.status = "cancelled"
    await db.commit()
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="order.reject",
            detail={"order_id": order.id, "reason": body.reason},
        )
    )
    await db.commit()
    if user:
        from app.services.notify import notify_user_telegram

        reason = f" Reason: {body.reason}" if body.reason else ""
        await notify_user_telegram(db, user, f"❌ Your order #{order.id} was rejected.{reason}")
    return {"detail": "Rejected"}
