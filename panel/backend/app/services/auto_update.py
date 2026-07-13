"""Daily fleet auto-update: queue git update for online workers once per day."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import WorkerNode
from app.services.worker_update import (
    get_update_state,
    request_update,
    version_supports_remote_update,
    worker_is_online,
)

log = logging.getLogger("panel.auto_update")

_task: asyncio.Task | None = None


def _seconds_until_next_utc_hour(hour: int) -> float:
    """Seconds until the next occurrence of ``hour`` UTC (0–23)."""
    h = max(0, min(23, int(hour)))
    now = datetime.now(timezone.utc)
    target = now.replace(hour=h, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


async def queue_daily_worker_updates() -> int:
    """Mark capable online workers for update. Returns how many were queued."""
    settings = get_settings()
    if not settings.worker_auto_update_enabled:
        return 0
    ref = (settings.worker_auto_update_ref or "main").strip() or "main"
    queued = 0
    async with SessionLocal() as db:
        rows = (await db.execute(select(WorkerNode).order_by(WorkerNode.id))).scalars().all()
        now = datetime.now(timezone.utc)
        for w in rows:
            if not worker_is_online(w, now=now):
                continue
            if not version_supports_remote_update(w.version or ""):
                continue
            state = get_update_state(w)
            if state["status"] in ("pending", "updating"):
                continue
            # Skip if this worker already succeeded on the same ref in the last ~20h
            # (avoids double-churn with host-side daily timers).
            if state["status"] == "success" and (state.get("ref") or "main") == ref:
                finished = state.get("finished_at")
                if finished:
                    try:
                        ts = datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if (now - ts).total_seconds() < 20 * 3600:
                            continue
                    except Exception:
                        pass
            request_update(w, ref=ref, message="Queued by daily auto-update")
            queued += 1
        if queued:
            await db.commit()
    if queued:
        log.info("Daily auto-update queued for %s worker(s) (ref=%s)", queued, ref)
    else:
        log.debug("Daily auto-update: nothing to queue")
    return queued


async def _loop() -> None:
    # Stagger first run a bit after boot so workers can heartbeat.
    await asyncio.sleep(90)
    while True:
        settings = get_settings()
        if not settings.worker_auto_update_enabled:
            await asyncio.sleep(3600)
            continue
        wait = _seconds_until_next_utc_hour(settings.worker_auto_update_hour_utc)
        log.info(
            "Worker fleet auto-update sleeping %.0fs until %02d:00 UTC",
            wait,
            settings.worker_auto_update_hour_utc,
        )
        await asyncio.sleep(wait)
        try:
            await queue_daily_worker_updates()
        except Exception:
            log.exception("Daily worker auto-update failed")
        # Avoid re-firing in the same hour if the job was fast
        await asyncio.sleep(60)


def start_auto_update_scheduler() -> None:
    global _task
    settings = get_settings()
    if not settings.worker_auto_update_enabled:
        log.info("Worker fleet auto-update disabled (WORKER_AUTO_UPDATE_ENABLED=0)")
        return
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop(), name="worker-auto-update")
    log.info(
        "Worker fleet auto-update enabled (daily %02d:00 UTC, ref=%s)",
        settings.worker_auto_update_hour_utc,
        settings.worker_auto_update_ref,
    )


async def stop_auto_update_scheduler() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
