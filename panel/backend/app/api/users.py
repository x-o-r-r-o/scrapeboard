import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_ready_user
from app.bot.runtime import bot_runtime
from app.bot.tg_auth import normalize_telegram_id
from app.core.database import get_db
from app.core.security import hash_password
from app.models import AuditLog, Package, User, UserWorker, WorkerNode
from app.schemas import MessageOut, UserCreate, UserOut, UserPermsSchema, UserUpdate
from app.services import billing as billing_svc
from app.services.billing import package_for_user, user_has_dedicated_worker
from app.services.perms import DEFAULT_USER_PERMS, ENGINE_OPTIONS, PERM_SCHEMA, normalize_perms
from app.services.worker_config import DEFAULT_WORKER_CONFIG, package_defaults_from_package


router = APIRouter(prefix="/users", tags=["users"])


async def _worker_ids_for(db: AsyncSession, user_id: int) -> list[int]:
    rows = (
        await db.execute(select(UserWorker.worker_id).where(UserWorker.user_id == user_id).order_by(UserWorker.worker_id))
    ).scalars().all()
    return [int(x) for x in rows]


async def _set_worker_ids(db: AsyncSession, user_id: int, worker_ids: list[int] | None) -> None:
    if worker_ids is None:
        return
    wanted = sorted({int(x) for x in worker_ids})
    if wanted:
        found = (
            await db.execute(select(WorkerNode.id).where(WorkerNode.id.in_(wanted)))
        ).scalars().all()
        missing = set(wanted) - {int(x) for x in found}
        if missing:
            raise HTTPException(404, f"Workers not found: {sorted(missing)}")
    existing = (
        await db.execute(select(UserWorker).where(UserWorker.user_id == user_id))
    ).scalars().all()
    for row in existing:
        await db.delete(row)
    for wid in wanted:
        db.add(UserWorker(user_id=user_id, worker_id=wid))

    # Seed newly pinned workers from the user's package when config is still empty/default
    if wanted:
        user = await db.get(User, user_id)
        pkg = await package_for_user(db, user) if user else None
        if pkg:
            defaults = package_defaults_from_package(pkg)
            workers = (
                await db.execute(select(WorkerNode).where(WorkerNode.id.in_(wanted)))
            ).scalars().all()
            for w in workers:
                cfg = w.worker_config or {}
                if not cfg or cfg == DEFAULT_WORKER_CONFIG:
                    w.worker_config = defaults


async def _user_out(db: AsyncSession, user: User, worker_ids: list[int] | None = None) -> UserOut:
    sub = await billing_svc.active_subscription(db, user)
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        totp_enabled=user.totp_enabled,
        telegram_id=user.telegram_id,
        perms=normalize_perms(user.perms),
        worker_ids=worker_ids or [],
        dedicated_worker=await user_has_dedicated_worker(db, user),
        created_at=user.created_at,
        subscription_package=sub.package_name if sub else None,
        subscription_id=sub.id if sub else None,
        subscription_expires_at=sub.expires_at if sub else None,
        has_active_subscription=bool(sub and billing_svc.subscription_is_live(sub)),
    )


@router.get("/perm-schema", response_model=UserPermsSchema)
async def perm_schema(_: User = Depends(require_admin), __: User = Depends(require_ready_user)):
    return UserPermsSchema(keys=PERM_SCHEMA, defaults=DEFAULT_USER_PERMS, engines=ENGINE_OPTIONS)


@router.get("", response_model=list[UserOut])
async def list_users(
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(select(User).order_by(User.id))).scalars().all()
    out = []
    for u in rows:
        out.append(await _user_out(db, u, await _worker_ids_for(db, u.id)))
    return out


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    admin: User = Depends(require_admin),
    _: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    if body.role == "user":
        tid = normalize_telegram_id(body.telegram_id, allow_group=False)
        if not tid:
            raise HTTPException(400, "telegram_id must be a numeric Telegram user id")
        existing_tg = (await db.execute(select(User).where(User.telegram_id == tid))).scalar_one_or_none()
        if existing_tg:
            raise HTTPException(400, f"Telegram id already linked to {existing_tg.username}")

        username = (body.username or f"tg_{tid}").strip()
        if (await db.execute(select(User).where(User.username == username))).scalar_one_or_none():
            raise HTTPException(400, "Username already exists")

        email = (str(body.email).strip().lower() if body.email else f"tg_{tid}@telegram.local")
        if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
            raise HTTPException(400, "Email already exists")

        password = body.password or secrets.token_urlsafe(24)
        base_perms = {**DEFAULT_USER_PERMS, "telegram_user": True, **(body.perms or {})}
        base_perms["telegram_user"] = True
        user = User(
            username=username,
            email=email,
            password_hash=hash_password(password),
            role="user",
            telegram_id=tid,
            perms=normalize_perms(base_perms),
            must_change_password=True,
            totp_enabled=False,
        )
        db.add(user)
        db.add(
            AuditLog(
                actor_id=admin.id,
                action="user.create",
                detail={"username": username, "telegram_id": tid, "kind": "telegram"},
            )
        )
        await db.commit()
        await db.refresh(user)

        if body.worker_ids is not None:
            await _set_worker_ids(db, user.id, body.worker_ids)
            await db.commit()

        if body.package_id:
            pkg = await db.get(Package, body.package_id)
            if not pkg:
                raise HTTPException(404, "Package not found")
            await billing_svc.activate_subscription(db, user, pkg, duration_days=body.duration_days)
            if body.notify:
                from app.services.notify import notify_user_telegram

                sub = await billing_svc.active_subscription(db, user)
                if sub:
                    await notify_user_telegram(
                        db,
                        user,
                        f"✅ Access granted: {pkg.name} until {sub.expires_at.date()}.",
                    )

        return await _user_out(db, user, await _worker_ids_for(db, user.id))

    # Admin panel account
    username = str(body.username).strip()
    email = str(body.email).strip().lower()
    if (await db.execute(select(User).where(User.username == username))).scalar_one_or_none():
        raise HTTPException(400, "Username already exists")
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise HTTPException(400, "Email already exists")
    tid: str | None = None
    if body.telegram_id is not None and str(body.telegram_id).strip() != "":
        tid = normalize_telegram_id(body.telegram_id, allow_group=False)
        if not tid:
            raise HTTPException(400, "telegram_id must be a numeric Telegram user id")
        clash = (await db.execute(select(User).where(User.telegram_id == tid))).scalar_one_or_none()
        if clash:
            raise HTTPException(400, f"Telegram id already linked to {clash.username}")
    user = User(
        username=username,
        email=email,
        password_hash=hash_password(body.password),
        role="admin",
        telegram_id=tid,
        perms=normalize_perms({**DEFAULT_USER_PERMS, **(body.perms or {})}),
        must_change_password=True,
        totp_enabled=False,
    )
    db.add(user)
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="user.create",
            detail={"username": username, "kind": "admin", "telegram_id": tid},
        )
    )
    await db.commit()
    await db.refresh(user)
    if body.worker_ids is not None:
        await _set_worker_ids(db, user.id, body.worker_ids)
        await db.commit()
    if tid:
        bot_runtime.invalidate_command_menu()
        await bot_runtime.refresh_command_menu(db)
    return await _user_out(db, user, await _worker_ids_for(db, user.id))


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    body: UserUpdate,
    admin: User = Depends(require_admin),
    _: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    prev_role = user.role
    prev_tg = user.telegram_id
    data = body.model_dump(exclude_unset=True)
    worker_ids = data.pop("worker_ids", None)
    if data.pop("reset_2fa", False):
        user.totp_secret = None
        user.totp_enabled = False
    if "password" in data and data["password"]:
        user.password_hash = hash_password(data.pop("password"))
        user.must_change_password = True
    else:
        data.pop("password", None)
    if "telegram_id" in data:
        tid = data.pop("telegram_id")
        if tid is None or tid == "":
            user.telegram_id = None
        else:
            tid_n = normalize_telegram_id(tid, allow_group=False)
            if not tid_n:
                raise HTTPException(400, "telegram_id must be numeric")
            clash = (
                await db.execute(select(User).where(User.telegram_id == tid_n, User.id != user.id))
            ).scalar_one_or_none()
            if clash:
                raise HTTPException(400, f"Telegram id already linked to {clash.username}")
            user.telegram_id = tid_n
    if "username" in data and data["username"]:
        new_username = str(data.pop("username")).strip()
        clash = (
            await db.execute(select(User).where(User.username == new_username, User.id != user.id))
        ).scalar_one_or_none()
        if clash:
            raise HTTPException(400, "Username already exists")
        user.username = new_username
    if "perms" in data and data["perms"] is not None:
        user.perms = normalize_perms(data.pop("perms"))
    for k, v in data.items():
        if k == "email" and v is not None:
            email = str(v).strip().lower()
            clash = (
                await db.execute(select(User).where(User.email == email, User.id != user.id))
            ).scalar_one_or_none()
            if clash:
                raise HTTPException(400, "Email already exists")
            setattr(user, k, email)
        elif v is not None:
            setattr(user, k, v)
    if worker_ids is not None:
        await _set_worker_ids(db, user.id, worker_ids)
    db.add(AuditLog(actor_id=admin.id, action="user.update", detail={"user_id": user_id}))
    await db.commit()
    await db.refresh(user)
    if user.role != prev_role or user.telegram_id != prev_tg or prev_role == "admin" or user.role == "admin":
        bot_runtime.invalidate_command_menu()
        await bot_runtime.refresh_command_menu(db)
    return await _user_out(db, user, await _worker_ids_for(db, user.id))


@router.delete("/{user_id}", response_model=MessageOut)
async def delete_user(
    user_id: int,
    admin: User = Depends(require_admin),
    _: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(400, "Cannot delete yourself")
    was_admin = user.role == "admin" and bool(user.telegram_id)
    await db.delete(user)
    db.add(AuditLog(actor_id=admin.id, action="user.delete", detail={"user_id": user_id}))
    await db.commit()
    if was_admin:
        bot_runtime.invalidate_command_menu()
        await bot_runtime.refresh_command_menu()
    return MessageOut(detail="Deleted")
