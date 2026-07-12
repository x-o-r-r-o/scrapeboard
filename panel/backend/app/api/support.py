"""Admin support ticket API — list, view, reply, close."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_ready_user
from app.core.database import get_db
from app.models import SupportMessage, SupportTicket, User
from app.schemas import (
    SupportCloseIn,
    SupportReplyIn,
    SupportTicketListOut,
    SupportTicketOut,
)
from app.services import support as support_svc

router = APIRouter(prefix="/support", tags=["support"])


def _list_item(ticket: SupportTicket, message_count: int) -> SupportTicketListOut:
    return SupportTicketListOut(
        id=ticket.id,
        user_id=ticket.user_id,
        telegram_id=ticket.telegram_id,
        message=ticket.message,
        status=ticket.status,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        closed_at=ticket.closed_at,
        message_count=message_count,
    )


@router.get("/tickets", response_model=list[SupportTicketListOut])
async def list_tickets(
    status: str = Query("open", pattern="^(open|closed|all)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    tickets = await support_svc.list_tickets(db, status=status, limit=limit, offset=offset)
    if not tickets:
        return []
    ids = [t.id for t in tickets]
    counts = dict(
        (
            await db.execute(
                select(SupportMessage.ticket_id, func.count(SupportMessage.id))
                .where(SupportMessage.ticket_id.in_(ids))
                .group_by(SupportMessage.ticket_id)
            )
        ).all()
    )
    return [_list_item(t, int(counts.get(t.id, 0))) for t in tickets]


@router.get("/tickets/{ticket_id}", response_model=SupportTicketOut)
async def get_ticket(
    ticket_id: int,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    ticket = await support_svc.get_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@router.post("/tickets/{ticket_id}/reply", response_model=SupportTicketOut)
async def reply_ticket(
    ticket_id: int,
    body: SupportReplyIn,
    admin: User = Depends(require_admin),
    _: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await support_svc.admin_reply(db, ticket_id=ticket_id, body=body.message, admin=admin)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ticket = await support_svc.get_ticket(db, ticket_id)
    assert ticket
    return ticket


@router.post("/tickets/{ticket_id}/close", response_model=SupportTicketOut)
async def close_ticket(
    ticket_id: int,
    body: SupportCloseIn | None = None,
    admin: User = Depends(require_admin),
    _: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    reason = body.reason if body else ""
    try:
        await support_svc.close_ticket(db, ticket_id=ticket_id, admin=admin, reason=reason)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    ticket = await support_svc.get_ticket(db, ticket_id)
    assert ticket
    return ticket
