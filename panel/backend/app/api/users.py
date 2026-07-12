from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_ready_user
from app.core.database import get_db
from app.core.security import hash_password
from app.models import AuditLog, User, UserWorker, WorkerNode
from app.schemas import MessageOut, UserCreate, UserOut, UserPermsSchema, UserUpdate
from app.services.billing import user_has_dedicated_worker
from app.services.perms import DEFAULT_USER_PERMS, ENGINE_OPTIONS, PERM_SCHEMA, normalize_perms

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


async def _user_out(db: AsyncSession, user: User, worker_ids: list[int] | None = None) -> UserOut:
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
    if (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none():
        raise HTTPException(400, "Username already exists")
    if (await db.execute(select(User).where(User.email == str(body.email)))).scalar_one_or_none():
        raise HTTPException(400, "Email already exists")
    user = User(
        username=body.username,
        email=str(body.email),
        password_hash=hash_password(body.password),
        role=body.role,
        telegram_id=body.telegram_id,
        perms=normalize_perms({**DEFAULT_USER_PERMS, **(body.perms or {})}),
        must_change_password=True,
        totp_enabled=False,
    )
    db.add(user)
    db.add(AuditLog(actor_id=admin.id, action="user.create", detail={"username": body.username}))
    await db.commit()
    await db.refresh(user)
    if body.worker_ids is not None:
        await _set_worker_ids(db, user.id, body.worker_ids)
        await db.commit()
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
            tid = str(tid).strip()
            clash = (
                await db.execute(select(User).where(User.telegram_id == tid, User.id != user.id))
            ).scalar_one_or_none()
            if clash:
                raise HTTPException(400, f"Telegram id already linked to {clash.username}")
            user.telegram_id = tid
    if "perms" in data and data["perms"] is not None:
        user.perms = normalize_perms(data.pop("perms"))
    for k, v in data.items():
        if k == "email" and v is not None:
            setattr(user, k, str(v))
        elif v is not None:
            setattr(user, k, v)
    if worker_ids is not None:
        await _set_worker_ids(db, user.id, worker_ids)
    db.add(AuditLog(actor_id=admin.id, action="user.update", detail={"user_id": user_id}))
    await db.commit()
    await db.refresh(user)
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
    await db.delete(user)
    db.add(AuditLog(actor_id=admin.id, action="user.delete", detail={"user_id": user_id}))
    await db.commit()
    return MessageOut(detail="Deleted")
