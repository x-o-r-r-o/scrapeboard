"""Sync Telegram Bot command menus with audience-aware scopes.

Default / private-chat menu = non-admin commands only.
Each linked admin gets BotCommandScopeChat with admin commands included
(when BotSettings.admin_commands_enabled).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.tg_auth import normalize_telegram_id
from app.models import BotCommand, BotSettings, User

log = logging.getLogger("bot.tg_commands")

# Telegram: command name without leading slash, 1–32 chars; description ≤256.
_TG_CMD_MAX = 100
_TG_DESC_MAX = 256


def _to_tg_command(row: BotCommand) -> dict[str, str] | None:
    raw = (row.command or "").strip().lower().split("@")[0]
    if not raw.startswith("/"):
        return None
    name = raw[1:]
    if not name or len(name) > 32 or not all(ch.isalnum() or ch == "_" for ch in name):
        return None
    desc = (row.title or row.description or name).strip() or name
    return {"command": name, "description": desc[:_TG_DESC_MAX]}


def _public_rows(rows: list[BotCommand]) -> list[BotCommand]:
    return [c for c in rows if (c.audience or "").strip().lower() != "admins"]


def _admin_menu_rows(rows: list[BotCommand], *, include_admin: bool) -> list[BotCommand]:
    if include_admin:
        return list(rows)
    return _public_rows(rows)


def _payload(rows: list[BotCommand]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in sorted(rows, key=lambda r: (r.sort_order, r.id)):
        item = _to_tg_command(row)
        if not item or item["command"] in seen:
            continue
        seen.add(item["command"])
        out.append(item)
        if len(out) >= _TG_CMD_MAX:
            break
    return out


async def _tg_post(token: str, method: str, body: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"https://api.telegram.org/bot{token}/{method}", json=body)
        return r.json()


async def _set_commands(token: str, commands: list[dict[str, str]], scope: dict[str, Any]) -> bool:
    data = await _tg_post(token, "setMyCommands", {"commands": commands, "scope": scope})
    if data.get("ok"):
        return True
    log.warning(
        "setMyCommands failed scope=%s: %s",
        scope,
        data.get("description") or data,
    )
    return False


async def _delete_commands(token: str, scope: dict[str, Any]) -> bool:
    data = await _tg_post(token, "deleteMyCommands", {"scope": scope})
    if data.get("ok"):
        return True
    # chat not found / nothing to delete — not fatal
    log.info("deleteMyCommands scope=%s: %s", scope, data.get("description") or data)
    return False


async def list_admin_chat_ids(db: AsyncSession) -> list[int]:
    admins = (
        await db.execute(
            select(User).where(
                User.role == "admin",
                User.is_active == True,  # noqa: E712
                User.telegram_id.isnot(None),
            )
        )
    ).scalars().all()
    ids: list[int] = []
    for a in admins:
        tid = normalize_telegram_id(a.telegram_id, allow_group=False)
        if tid and tid.lstrip("-").isdigit():
            ids.append(int(tid))
    return ids


async def sync_telegram_command_menu(
    db: AsyncSession,
    token: str,
    *,
    previous_admin_chats: set[int] | None = None,
) -> set[int]:
    """Push audience-scoped command menus to Telegram. Returns admin chat ids set."""
    token = (token or "").strip()
    if not token:
        return set()

    settings = await db.get(BotSettings, 1)
    include_admin = bool(settings and settings.admin_commands_enabled)

    rows = (
        await db.execute(
            select(BotCommand).where(BotCommand.enabled == True).order_by(BotCommand.sort_order, BotCommand.id)  # noqa: E712
        )
    ).scalars().all()

    public = _payload(_public_rows(list(rows)))
    admin_menu = _payload(_admin_menu_rows(list(rows), include_admin=include_admin))

    # Default + all private chats: never expose admin-audience commands.
    await _set_commands(token, public, {"type": "default"})
    await _set_commands(token, public, {"type": "all_private_chats"})
    # Groups also get the public list (no admin CRUD in group menus by default).
    await _set_commands(token, public, {"type": "all_group_chats"})

    current_admins = set(await list_admin_chat_ids(db))
    prev = previous_admin_chats or set()

    for chat_id in prev - current_admins:
        await _delete_commands(token, {"type": "chat", "chat_id": chat_id})

    applied: set[int] = set()
    for chat_id in current_admins:
        scope = {"type": "chat", "chat_id": chat_id}
        if include_admin and admin_menu != public:
            ok = await _set_commands(token, admin_menu, scope)
            if ok:
                applied.add(chat_id)
            else:
                # Chat unknown until admin DMs the bot once — leave default public menu.
                log.info(
                    "Admin command scope not applied for chat_id=%s "
                    "(admin must open a private chat with the bot once)",
                    chat_id,
                )
        else:
            # Fall back to public (or clear chat override).
            await _delete_commands(token, scope)

    log.info(
        "Telegram command menu synced: public=%d admin_cmds=%d admin_chats=%d/%d",
        len(public),
        len(admin_menu) if include_admin else 0,
        len(applied),
        len(current_admins),
    )
    return current_admins
