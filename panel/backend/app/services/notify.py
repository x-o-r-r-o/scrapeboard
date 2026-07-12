"""Telegram send helpers used by bot runtime and job finalization."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BotSettings, User
from app.bot.tg_auth import normalize_telegram_id

log = logging.getLogger("bot.notify")


async def bot_token(db: AsyncSession) -> str | None:
    s = await db.get(BotSettings, 1)
    if s and s.enabled and s.token:
        return s.token
    return None


async def send_text(
    token: str,
    chat_id: int | str,
    text: str,
    *,
    reply_markup: dict | None = None,
) -> None:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            payload: dict = {"chat_id": chat_id, "text": text[:4000]}
            if reply_markup is not None:
                import json

                payload["reply_markup"] = json.dumps(reply_markup)
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload,
            )
            data = r.json()
            if not data.get("ok"):
                desc = str(data.get("description") or "sendMessage failed")
                code = data.get("error_code")
                log.warning("sendMessage rejected chat_id=%s: %s%s", chat_id, f"{code}: " if code else "", desc)
    except Exception:
        log.exception("send_text failed")


async def send_document(token: str, chat_id: int | str, path: Path, caption: str = "") -> bool:
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            with open(path, "rb") as fh:
                r = await client.post(
                    f"https://api.telegram.org/bot{token}/sendDocument",
                    data={"chat_id": chat_id, "caption": caption[:1000]},
                    files={"document": (path.name, fh)},
                )
            return bool(r.json().get("ok"))
    except Exception:
        log.exception("send_document failed")
        return False


async def send_photo(
    token: str,
    chat_id: int | str,
    photo: bytes,
    caption: str = "",
    *,
    filename: str = "qr.png",
) -> bool:
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data={"chat_id": chat_id, "caption": (caption or "")[:1024]},
                files={"photo": (filename, photo, "image/png")},
            )
            data = r.json()
            if not data.get("ok"):
                log.warning("sendPhoto rejected: %s", data.get("description"))
            return bool(data.get("ok"))
    except Exception:
        log.exception("send_photo failed")
        return False


async def notify_user_telegram(
    db: AsyncSession,
    user: User,
    text: str,
    document: Path | None = None,
    *,
    reply_markup: dict | None = None,
) -> None:
    token = await bot_token(db)
    tid = normalize_telegram_id(user.telegram_id, allow_group=False)
    if not token or not tid:
        return
    settings = await db.get(BotSettings, 1)
    await send_text(token, tid, text, reply_markup=reply_markup)
    if document and settings and settings.deliver_results_telegram:
        await send_document(token, tid, document, caption=document.name)


async def notify_admins_telegram(db: AsyncSession, text: str) -> None:
    token = await bot_token(db)
    if not token:
        return
    admins = (
        await db.execute(
            select(User).where(
                User.role == "admin",
                User.is_active == True,  # noqa: E712
                User.telegram_id.isnot(None),
            )
        )
    ).scalars().all()
    for a in admins:
        tid = normalize_telegram_id(a.telegram_id, allow_group=False)
        if tid:
            await send_text(token, tid, text)
