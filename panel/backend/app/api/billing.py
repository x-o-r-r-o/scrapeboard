from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_ready_user
from app.core.database import get_db
from app.models import BillingSettings, Order, Package, PaymentTxid, User
from app.schemas import (
    BillingSettingsOut,
    BillingSettingsUpdate,
    PackageCreate,
    PackageOut,
    PackageUpdate,
    SubscriptionOut,
)
from app.services import billing as billing_svc

router = APIRouter(tags=["billing"])


class BuyRequest(BaseModel):
    package_slug: str


class PaidRequest(BaseModel):
    txid: str


class GrantRequest(BaseModel):
    user_id: int
    package_id: int


class ApproveRequest(BaseModel):
    order_id: int


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


async def _billing(db: AsyncSession) -> BillingSettings:
    return await billing_svc.get_billing(db)


def _sub_out(sub) -> SubscriptionOut:
    now = datetime.now(timezone.utc)
    exp = sub.expires_at if sub.expires_at.tzinfo else sub.expires_at.replace(tzinfo=timezone.utc)
    days = max(0, (exp - now).total_seconds() / 86400)
    return SubscriptionOut(
        id=sub.id,
        package_name=sub.package_name,
        threads=sub.threads,
        max_upload_mb=sub.max_upload_mb,
        tier=sub.tier,
        starts_at=sub.starts_at,
        expires_at=sub.expires_at,
        is_active=sub.is_active and exp > now,
        days_left=days,
    )


@router.get("/packages", response_model=list[PackageOut])
async def list_packages(user: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    q = select(Package).order_by(Package.tier)
    if user.role != "admin":
        q = q.where(Package.is_active == True)  # noqa: E712
    return (await db.execute(q)).scalars().all()


@router.post("/packages", response_model=PackageOut)
async def create_package(
    body: PackageCreate,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    if (await db.execute(select(Package).where(Package.slug == body.slug))).scalar_one_or_none():
        raise HTTPException(400, "Slug exists")
    pkg = Package(**body.model_dump())
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
    pkg = await db.get(Package, package_id)
    if not pkg:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(pkg, k, v)
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
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(b, k, v)
    await db.commit()
    return await get_billing_settings(_, __, db)


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


@router.post("/subscriptions/grant")
async def grant_subscription(
    body: GrantRequest,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, body.user_id)
    pkg = await db.get(Package, body.package_id)
    if not user or not pkg:
        raise HTTPException(404, "User or package not found")
    sub = await billing_svc.activate_subscription(db, user, pkg)
    return {"detail": "Granted", "expires_at": sub.expires_at.isoformat()}


@router.post("/orders/buy")
async def buy_package(
    body: BuyRequest,
    user: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    b = await _billing(db)
    if not b.enabled:
        raise HTTPException(400, "Billing is disabled")
    pkg = (await db.execute(select(Package).where(Package.slug == body.package_slug, Package.is_active == True))).scalar_one_or_none()  # noqa: E712
    if not pkg:
        raise HTTPException(404, "Package not found")
    ok, why = await billing_svc.can_purchase(db, user, pkg)
    if not ok:
        raise HTTPException(400, why)
    method = "usdt" if b.usdt_enabled else ("manual" if b.manual_enabled else "")
    order = await billing_svc.create_order(db, user, pkg, method)
    instructions = await billing_svc.payment_instructions(db, pkg)
    return {"order_id": order.id, "instructions": instructions, "package": PackageOut.model_validate(pkg)}


@router.post("/orders/paid")
async def submit_paid(
    body: PaidRequest,
    user: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    b = await _billing(db)
    if not b.usdt_enabled or not b.usdt_wallet:
        raise HTTPException(400, "USDT payments not enabled")
    if not billing_svc.valid_txid(body.txid):
        raise HTTPException(400, "Invalid TRON transaction id")
    if await billing_svc.txid_used(db, body.txid):
        raise HTTPException(400, "Transaction already used")

    order = (
        await db.execute(
            select(Order).where(Order.user_id == user.id, Order.status == "pending").order_by(Order.id.desc())
        )
    ).scalars().first()
    if not order:
        raise HTTPException(400, "No pending order — buy a package first")
    pkg = await db.get(Package, order.package_id)
    if not pkg:
        raise HTTPException(400, "Package missing")

    ok, detail, amount = await billing_svc.verify_trc20_payment(
        body.txid,
        b.usdt_wallet,
        pkg.price_usdt,
        b.usdt_api_base,
        b.usdt_api_key,
        b.usdt_contract,
    )
    if not ok:
        raise HTTPException(400, detail)

    db.add(PaymentTxid(txid=body.txid, user_id=user.id, order_id=order.id))
    order.status = "paid"
    order.txid = body.txid
    order.payment_method = "usdt"
    await db.commit()
    sub = await billing_svc.activate_subscription(db, user, pkg)
    return {
        "detail": f"Payment verified ({amount:.2f} USDT)",
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
    await db.commit()
    sub = await billing_svc.activate_subscription(db, user, pkg)
    from app.services.notify import notify_user_telegram

    await notify_user_telegram(
        db,
        user,
        f"✅ Your {pkg.name} subscription is active until {sub.expires_at.date()}.",
    )
    return {"detail": "Approved", "expires_at": sub.expires_at.isoformat()}
