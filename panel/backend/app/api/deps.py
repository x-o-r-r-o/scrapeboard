from datetime import datetime, timedelta, timezone

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import decode_access_token
from app.models import LoginAttempt, SecuritySettings, User

bearer = HTTPBearer(auto_error=False)


async def get_security_settings(db: AsyncSession) -> SecuritySettings:
    row = await db.get(SecuritySettings, 1)
    if not row:
        row = SecuritySettings(id=1)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def is_locked_out(db: AsyncSession, username: str, ip: str, sec: SecuritySettings) -> bool:
    since = datetime.now(timezone.utc) - timedelta(minutes=sec.lockout_minutes)
    # Lock by username+IP so one IP cannot lock every account, and credentials
    # sprayed from many IPs still trip per-IP limits.
    q = await db.execute(
        select(LoginAttempt)
        .where(
            LoginAttempt.username == username,
            LoginAttempt.ip_address == (ip or ""),
            LoginAttempt.created_at >= since,
        )
        .order_by(LoginAttempt.created_at.desc())
        .limit(sec.max_login_failures)
    )
    attempts = list(q.scalars().all())
    if len(attempts) < sec.max_login_failures:
        return False
    return all(not a.success for a in attempts)


async def record_login_attempt(db: AsyncSession, username: str, ip: str, success: bool) -> None:
    db.add(LoginAttempt(username=username, ip_address=ip, success=success))
    await db.commit()


async def verify_recaptcha(token: str | None, sec: SecuritySettings, remote_ip: str | None = None) -> None:
    if sec.recaptcha_mode == "none":
        return
    if not sec.recaptcha_secret_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "reCAPTCHA is enabled but not configured")
    if not token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "reCAPTCHA token required")

    data = {"secret": sec.recaptcha_secret_key, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post("https://www.google.com/recaptcha/api/siteverify", data=data)
        payload = r.json()
    if not payload.get("success"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "reCAPTCHA verification failed")
    if sec.recaptcha_mode == "v3":
        score = float(payload.get("score") or 0)
        if score < sec.recaptcha_v3_min_score:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"reCAPTCHA score too low ({score})")


async def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    payload = decode_access_token(creds.credentials)
    if not payload or "sub" not in payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    user = await db.get(User, int(payload["sub"]))
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User inactive or missing")
    request.state.user = user
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return user


async def require_ready_user(user: User = Depends(get_current_user)) -> User:
    """User must have finished password change + 2FA setup."""
    if user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Password change required")
    if not user.totp_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "2FA setup required")
    return user


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


DEFAULT_USER_PERMS = {
    "can_run": True,
    "can_stop": True,
    "can_upload_inputs": True,
    "max_threads": 4,
    "allowed_engines": "all",
}


def effective_perms(user: User) -> dict:
    if user.role == "admin":
        return {**DEFAULT_USER_PERMS, "can_run": True, "can_stop": True, "can_upload_inputs": True, "max_threads": 999}
    merged = {**DEFAULT_USER_PERMS, **(user.perms or {})}
    return merged
