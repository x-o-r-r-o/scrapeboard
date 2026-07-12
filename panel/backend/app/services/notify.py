"""Telegram send helpers used by bot runtime and job finalization."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BotSettings, User

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


async def notify_user_telegram(db: AsyncSession, user: User, text: str, document: Path | None = None) -> None:
    token = await bot_token(db)
    if not token or not user.telegram_id:
        return
    settings = await db.get(BotSettings, 1)
    await send_text(token, user.telegram_id, text)
    if document and settings and settings.deliver_results_telegram:
        await send_document(token, user.telegram_id, document, caption=document.name)


async def notify_admins_telegram(db: AsyncSession, text: str) -> None:
    token = await bot_token(db)
    if not token:
        return
    admins = (await db.execute(select(User).where(User.role == "admin", User.telegram_id.isnot(None)))).scalars().all()
    for a in admins:
        if a.telegram_id:
            await send_text(token, a.telegram_id, text)
