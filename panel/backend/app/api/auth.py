from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    client_ip,
    get_current_user,
    get_security_settings,
    is_locked_out,
    record_login_attempt,
    require_ready_user,
    verify_recaptcha,
)
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    generate_totp_secret,
    hash_password,
    totp_uri,
    verify_password,
    verify_totp,
)
from app.models import AuditLog, User
from app.schemas import (
    LoginRequest,
    MessageOut,
    PasswordChangeRequest,
    TokenResponse,
    TotpEnableRequest,
    TotpSetupResponse,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/public-config")
async def public_config(db: AsyncSession = Depends(get_db)):
    """Public login page config (reCAPTCHA mode + site key only). No registration."""
    sec = await get_security_settings(db)
    return {
        "registration_enabled": False,
        "recaptcha_mode": sec.recaptcha_mode,
        "recaptcha_site_key": sec.recaptcha_site_key if sec.recaptcha_mode != "none" else "",
        "totp_required": True,
    }


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    ip = client_ip(request)
    sec = await get_security_settings(db)
    if await is_locked_out(db, body.username, ip, sec):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed logins. Try again later.")

    await verify_recaptcha(body.recaptcha_token, sec, ip)

    user = (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash) or not user.is_active:
        await record_login_attempt(db, body.username, ip, False)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    if user.totp_enabled:
        if not body.totp_code or not verify_totp(user.totp_secret or "", body.totp_code):
            await record_login_attempt(db, body.username, ip, False)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid 2FA code")
    elif body.totp_code:
        # ignore stray code before setup
        pass

    await record_login_attempt(db, body.username, ip, True)
    db.add(AuditLog(actor_id=user.id, action="login", ip_address=ip, detail={}))
    await db.commit()

    token = create_access_token(str(user.id), {"role": user.role})
    return TokenResponse(
        access_token=token,
        must_change_password=user.must_change_password,
        must_setup_2fa=not user.totp_enabled,
        totp_setup_required=not user.totp_enabled,
    )


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


@router.post("/change-password", response_model=MessageOut)
async def change_password(
    body: PasswordChangeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password incorrect")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    await db.commit()
    return MessageOut(detail="Password updated")


@router.post("/2fa/setup", response_model=TotpSetupResponse)
async def setup_2fa(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Change password before enabling 2FA")
    if user.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "2FA already enabled")
    secret = generate_totp_secret()
    user.totp_secret = secret
    await db.commit()
    return TotpSetupResponse(secret=secret, otpauth_uri=totp_uri(secret, user.username))


@router.post("/2fa/enable", response_model=MessageOut)
async def enable_2fa(
    body: TotpEnableRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Change password before enabling 2FA")
    if not user.totp_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Call /2fa/setup first")
    if not verify_totp(user.totp_secret, body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid 2FA code")
    user.totp_enabled = True
    await db.commit()
    return MessageOut(detail="2FA enabled")


@router.get("/ready")
async def ready(user: User = Depends(require_ready_user)):
    return {"ok": True, "username": user.username, "role": user.role}
