"""Support ticket create / reply / close — shared by Telegram bot and panel API."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.tg_auth import normalize_telegram_id, resolve_support_chat_id
from app.models import BotSettings, SupportMessage, SupportTicket, User
from app.services.notify import bot_token, send_text

log = logging.getLogger("bot.support")

TICKET_HEADER_RE = re.compile(r"^Support\s+#(\d+)\b", re.IGNORECASE)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_ticket_id_from_forward(text: str | None) -> int | None:
    """Extract ticket id from admin-forwarded header 'Support #N from …'."""
    if not text:
        return None
    m = TICKET_HEADER_RE.match(text.strip())
    return int(m.group(1)) if m else None


async def get_ticket(db: AsyncSession, ticket_id: int) -> SupportTicket | None:
    return (
        await db.execute(
            select(SupportTicket)
            .where(SupportTicket.id == ticket_id)
            .options(selectinload(SupportTicket.messages))
        )
    ).scalar_one_or_none()


async def open_ticket_for_telegram(db: AsyncSession, telegram_id: str) -> SupportTicket | None:
    tid = normalize_telegram_id(telegram_id, allow_group=False) or telegram_id
    return (
        await db.execute(
            select(SupportTicket)
            .where(SupportTicket.telegram_id == tid, SupportTicket.status == "open")
            .order_by(SupportTicket.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def list_tickets(
    db: AsyncSession,
    *,
    status: str | None = "open",
    limit: int = 50,
    offset: int = 0,
) -> list[SupportTicket]:
    q = select(SupportTicket).order_by(
        func.coalesce(SupportTicket.updated_at, SupportTicket.created_at).desc(),
        SupportTicket.id.desc(),
    )
    if status and status != "all":
        q = q.where(SupportTicket.status == status)
    q = q.offset(max(0, offset)).limit(max(1, min(limit, 200)))
    return list((await db.execute(q)).scalars().all())


async def _touch(ticket: SupportTicket) -> None:
    ticket.updated_at = utcnow()


async def _notify_user(db: AsyncSession, ticket: SupportTicket, text: str) -> bool:
    token = await bot_token(db)
    tid = normalize_telegram_id(ticket.telegram_id, allow_group=False)
    if not token or not tid:
        log.warning("Cannot notify ticket #%s user (token or telegram_id missing)", ticket.id)
        return False
    await send_text(token, tid, text)
    return True


async def _notify_support_chat(db: AsyncSession, settings: BotSettings, text: str) -> bool:
    token = await bot_token(db)
    support_to = await resolve_support_chat_id(db, settings)
    if not token or not support_to:
        log.warning("Support notify skipped — no token or support_chat_id")
        return False
    await send_text(token, support_to, text)
    return True


def format_ticket_header(ticket: SupportTicket, *, follow_up: bool = False) -> str:
    kind = "follow-up" if follow_up else "new"
    return f"Support #{ticket.id} ({kind}) from {ticket.telegram_id} [{ticket.status}]"


def format_thread(ticket: SupportTicket, messages: list[SupportMessage] | None = None) -> str:
    msgs = messages if messages is not None else list(ticket.messages or [])
    lines = [
        f"Ticket #{ticket.id} · {ticket.status}",
        f"User tg={ticket.telegram_id} · user_id={ticket.user_id or '-'}",
        f"Opened: {ticket.created_at}",
        "",
        f"[user] {ticket.message}",
    ]
    for m in msgs:
        who = "admin" if m.sender == "admin" else "user"
        lines.append(f"[{who}] {m.body}")
    if ticket.status == "closed" and ticket.closed_at:
        lines.append(f"\nClosed at {ticket.closed_at}")
    return "\n".join(lines)


async def create_or_append_user_message(
    db: AsyncSession,
    *,
    settings: BotSettings,
    user: User | None,
    telegram_id: str,
    body: str,
) -> tuple[SupportTicket, SupportMessage | None, bool]:
    """Create a new open ticket or append to the user's existing open ticket.

    Returns (ticket, follow_up_message_or_None, created_new).
    """
    body = (body or "").strip() or "(empty)"
    tid = normalize_telegram_id(telegram_id, allow_group=False) or str(telegram_id)
    existing = await open_ticket_for_telegram(db, tid)
    if existing:
        msg = SupportMessage(ticket_id=existing.id, sender="user", body=body)
        db.add(msg)
        await _touch(existing)
        await db.commit()
        await db.refresh(existing)
        await db.refresh(msg)
        await _notify_support_chat(
            db,
            settings,
            f"{format_ticket_header(existing, follow_up=True)}:\n{body}\n\nReply: /reply {existing.id} …\nClose: /close {existing.id}",
        )
        return existing, msg, False

    ticket = SupportTicket(
        user_id=user.id if user else None,
        telegram_id=tid,
        message=body,
        status="open",
        updated_at=utcnow(),
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    await _notify_support_chat(
        db,
        settings,
        f"{format_ticket_header(ticket)}:\n{body}\n\nReply: /reply {ticket.id} …\nClose: /close {ticket.id}",
    )
    return ticket, None, True


async def admin_reply(
    db: AsyncSession,
    *,
    ticket_id: int,
    body: str,
    admin: User | None,
) -> SupportTicket:
    body = (body or "").strip()
    if not body:
        raise ValueError("Reply message is empty.")
    ticket = await get_ticket(db, ticket_id)
    if not ticket:
        raise LookupError(f"Ticket #{ticket_id} not found.")
    if ticket.status != "open":
        raise ValueError(f"Ticket #{ticket_id} is closed. Ask the user to open a new one with /support.")
    msg = SupportMessage(
        ticket_id=ticket.id,
        sender="admin",
        admin_user_id=admin.id if admin else None,
        body=body,
    )
    db.add(msg)
    await _touch(ticket)
    await db.commit()
    await db.refresh(ticket)
    await _notify_user(
        db,
        ticket,
        f"💬 Support reply on ticket #{ticket.id}:\n{body}",
    )
    return ticket


async def close_ticket(
    db: AsyncSession,
    *,
    ticket_id: int,
    admin: User | None,
    reason: str = "",
) -> SupportTicket:
    ticket = await get_ticket(db, ticket_id)
    if not ticket:
        raise LookupError(f"Ticket #{ticket_id} not found.")
    if ticket.status == "closed":
        return ticket
    ticket.status = "closed"
    ticket.closed_at = utcnow()
    ticket.closed_by_id = admin.id if admin else None
    await _touch(ticket)
    note = (reason or "").strip()
    if note:
        db.add(
            SupportMessage(
                ticket_id=ticket.id,
                sender="admin",
                admin_user_id=admin.id if admin else None,
                body=f"[closed] {note}",
            )
        )
    await db.commit()
    await db.refresh(ticket)
    text = f"✅ Support ticket #{ticket.id} was closed."
    if note:
        text += f"\n{note}"
    text += "\nTo contact support again, send /support <message>."
    await _notify_user(db, ticket, text)
    return ticket
