from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DEFAULT_USER_PERMS, require_admin, require_ready_user
from app.core.database import get_db
from app.core.security import hash_password
from app.models import AuditLog, User
from app.schemas import MessageOut, UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
async def list_users(
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(select(User).order_by(User.id))).scalars().all()
    return rows


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
        perms={**DEFAULT_USER_PERMS, **(body.perms or {})},
        must_change_password=True,
        totp_enabled=False,
    )
    db.add(user)
    db.add(AuditLog(actor_id=admin.id, action="user.create", detail={"username": body.username}))
    await db.commit()
    await db.refresh(user)
    return user


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
    if data.pop("reset_2fa", False):
        user.totp_secret = None
        user.totp_enabled = False
    if "password" in data and data["password"]:
        user.password_hash = hash_password(data.pop("password"))
        user.must_change_password = True
    else:
        data.pop("password", None)
    for k, v in data.items():
        if k == "email" and v is not None:
            setattr(user, k, str(v))
        elif v is not None:
            setattr(user, k, v)
    db.add(AuditLog(actor_id=admin.id, action="user.update", detail={"user_id": user_id}))
    await db.commit()
    await db.refresh(user)
    return user


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
