"""Telegram user/admin identity helpers — normalize IDs and resolve admin access."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BotSettings, User

log = logging.getLogger("bot.tg_auth")


def normalize_telegram_id(value: Any, *, allow_group: bool = True) -> str | None:
    """Coerce a Telegram user/chat id to a canonical digit string.

    Accepts int/float/str (including accidental scientific notation). User ids are
    positive digits; group/supergroup chat ids may be negative (e.g. -100…).
    Returns None if the value is not a usable numeric id.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        s = str(value)
    elif isinstance(value, float):
        if not value.is_integer():
            return None
        s = str(int(value))
    else:
        s = str(value).strip().replace(" ", "").replace(",", "")
        if not s or s.startswith("@"):
            return None
        if "e" in s.lower() or "." in s:
            try:
                f = float(s)
            except ValueError:
                return None
            if not f.is_integer():
                return None
            s = str(int(f))
    if s.startswith("-"):
        body = s[1:]
        if not body.isdigit() or not allow_group:
            return None
        return f"-{body}"
    if not s.isdigit():
        return None
    # Strip accidental leading zeros while keeping "0" invalid for Telegram users.
    if s.startswith("0") and len(s) > 1:
        s = str(int(s))
    return s


async def find_user_by_telegram(db: AsyncSession, raw_id: Any) -> User | None:
    """Match User.telegram_id to an incoming Telegram from.id (string-safe)."""
    from sqlalchemy import func

    tid = normalize_telegram_id(raw_id, allow_group=False)
    if not tid:
        return None
    user = (await db.execute(select(User).where(User.telegram_id == tid))).scalar_one_or_none()
    if user:
        return user
    # Heal whitespace-padded values stored historically.
    user = (
        await db.execute(select(User).where(func.trim(User.telegram_id) == tid))
    ).scalar_one_or_none()
    if user and user.telegram_id != tid:
        log.info("Healing User#%s telegram_id %r → %s", user.id, user.telegram_id, tid)
        user.telegram_id = tid
        await db.commit()
        await db.refresh(user)
    return user


@dataclass(frozen=True)
class AdminResolve:
    user: User | None
    ok: bool
    reason: str  # machine key
    message: str  # human-facing Telegram reply


def admin_deny_message(reason: str, *, uid: Any, user: User | None = None) -> str:
    tid = normalize_telegram_id(uid, allow_group=False) or str(uid)
    if reason == "no_panel_user":
        return (
            f"⛔ Not linked to a panel account. Your Telegram id is {tid}.\n"
            "Set this id on Users → admin → Telegram ID, enable Admin commands in Bot Builder, "
            "and keep the bot Live."
        )
    if reason == "account_disabled":
        return "⛔ Your account is disabled. Contact support."
    if reason == "not_admin_role":
        role = user.role if user else "?"
        name = user.username if user else "?"
        return (
            f"⛔ Linked as {name} (role={role}), not admin.\n"
            f"Your Telegram id is {tid}. Set role=admin on that Users row."
        )
    if reason == "admin_commands_disabled":
        return (
            "⛔ Admin Telegram commands are disabled.\n"
            "Turn on “Admin Telegram commands” in Bot Builder and Save."
        )
    if reason == "no_telegram_id":
        return "⛔ Admin account has no telegram_id set."
    return f"⛔ Admin access denied ({reason})."


async def resolve_admin(
    db: AsyncSession,
    raw_telegram_id: Any,
    settings: BotSettings,
) -> AdminResolve:
    """Require linked role=admin + active + BotSettings.admin_commands_enabled."""
    user = await find_user_by_telegram(db, raw_telegram_id)
    if not user:
        reason = "no_panel_user"
        return AdminResolve(None, False, reason, admin_deny_message(reason, uid=raw_telegram_id))
    if not user.is_active:
        reason = "account_disabled"
        return AdminResolve(user, False, reason, admin_deny_message(reason, uid=raw_telegram_id, user=user))
    if user.role != "admin":
        reason = "not_admin_role"
        return AdminResolve(user, False, reason, admin_deny_message(reason, uid=raw_telegram_id, user=user))
    if not settings.admin_commands_enabled:
        reason = "admin_commands_disabled"
        log.info(
            "Admin command denied for tg=%s user=%s: admin_commands_enabled=False",
            normalize_telegram_id(raw_telegram_id, allow_group=False),
            user.username,
        )
        return AdminResolve(user, False, reason, admin_deny_message(reason, uid=raw_telegram_id, user=user))
    return AdminResolve(user, True, "ok", "")


async def first_admin_telegram_id(db: AsyncSession) -> str | None:
    """First enabled admin with a usable telegram_id (lowest id)."""
    rows = (
        await db.execute(
            select(User)
            .where(User.role == "admin", User.is_active == True, User.telegram_id.is_not(None))  # noqa: E712
            .order_by(User.id)
        )
    ).scalars().all()
    for u in rows:
        tid = normalize_telegram_id(u.telegram_id, allow_group=False)
        if tid:
            return tid
    return None


async def resolve_support_chat_id(db: AsyncSession, settings: BotSettings) -> str | None:
    """Support notify target: BotSettings.support_chat_id, else first admin telegram_id."""
    configured = normalize_telegram_id(settings.support_chat_id or "", allow_group=True)
    if configured:
        return configured
    return await first_admin_telegram_id(db)


async def admin_setup_hint(db: AsyncSession, settings: BotSettings) -> str:
    """Short Bot Builder / status hint for admin Telegram access."""
    parts: list[str] = []
    if not settings.enabled:
        parts.append("Live is off")
    if not (settings.token or "").strip():
        parts.append("no bot token")
    if not settings.admin_commands_enabled:
        parts.append("Admin commands toggle is off")
    linked = await first_admin_telegram_id(db)
    if not linked:
        parts.append("no enabled admin has a Telegram ID")
    if not parts:
        return (
            f"Admin Telegram ready (sample admin tg={linked}). "
            "DM the bot /whoami then /admin."
        )
    return "Admin access needs: " + "; ".join(parts) + "."
