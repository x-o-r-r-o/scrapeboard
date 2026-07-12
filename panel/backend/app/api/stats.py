"""Live dashboard / fleet statistics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_ready_user
from app.core.database import get_db
from app.models import Job, JobChunk, Subscription, User, WorkerNode

router = APIRouter(prefix="/stats", tags=["stats"])

ONLINE_WINDOW = timedelta(seconds=90)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _online(w: WorkerNode, now: datetime) -> bool:
    ts = _aware(w.last_seen_at)
    return bool(ts and now - ts < ONLINE_WINDOW)


def _worker_payload(w: WorkerNode, now: datetime, active_leases: int) -> dict:
    return {
        "id": w.id,
        "name": w.name,
        "online": _online(w, now),
        "is_enabled": w.is_enabled,
        "is_draining": w.is_draining,
        "status": (
            "disabled"
            if not w.is_enabled
            else "draining"
            if w.is_draining
            else "online"
            if _online(w, now)
            else "offline"
        ),
        "last_seen_at": w.last_seen_at.isoformat() if w.last_seen_at else None,
        "cpu_percent": float(w.cpu_percent or 0),
        "mem_percent": float(w.mem_percent or 0),
        "disk_percent": float(getattr(w, "disk_percent", 0) or 0),
        "mem_used_gb": float(getattr(w, "mem_used_gb", 0) or 0),
        "mem_total_gb": float(getattr(w, "mem_total_gb", 0) or 0),
        "disk_used_gb": float(getattr(w, "disk_used_gb", 0) or 0),
        "disk_total_gb": float(getattr(w, "disk_total_gb", 0) or 0),
        "load_avg_1": float(getattr(w, "load_avg_1", 0) or 0),
        "load_avg_5": float(getattr(w, "load_avg_5", 0) or 0),
        "load_avg_15": float(getattr(w, "load_avg_15", 0) or 0),
        "host_os": str(getattr(w, "host_os", "") or ""),
        "hostname": str(getattr(w, "hostname", "") or ""),
        "version": w.version or "",
        "max_browsers": w.max_browsers,
        "active_leases": active_leases,
        "load_ratio": round(active_leases / max(1, w.max_browsers), 3),
    }


@router.get("/live")
async def live_stats(user: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    workers = (await db.execute(select(WorkerNode).order_by(WorkerNode.id))).scalars().all()
    lease_rows = (
        await db.execute(
            select(JobChunk.worker_id, func.count())
            .where(JobChunk.state == "leased", JobChunk.worker_id.is_not(None))
            .group_by(JobChunk.worker_id)
        )
    ).all()
    leases_by_worker = {int(wid): int(cnt) for wid, cnt in lease_rows if wid is not None}

    worker_payloads = [_worker_payload(w, now, leases_by_worker.get(w.id, 0)) for w in workers]
    online_workers = [w for w in worker_payloads if w["online"]]
    busy_workers = [w for w in online_workers if w["active_leases"] > 0]

    def _avg(key: str) -> float:
        if not online_workers:
            return 0.0
        return round(sum(float(w[key]) for w in online_workers) / len(online_workers), 1)

    # Jobs
    async def _count_jobs(status: str | None = None, owner_id: int | None = None) -> int:
        q = select(func.count()).select_from(Job)
        if status:
            q = q.where(Job.status == status)
        if owner_id is not None:
            q = q.where(Job.owner_id == owner_id)
        return int((await db.execute(q)).scalar_one() or 0)

    if user.role == "admin":
        jobs_queued = await _count_jobs("queued")
        jobs_running = await _count_jobs("running")
        jobs_completed = await _count_jobs("completed")
        jobs_failed = await _count_jobs("failed")
        jobs_stopped = await _count_jobs("stopped")
        rows_today = int(
            (
                await db.execute(
                    select(func.coalesce(func.sum(Job.rows_saved), 0)).where(
                        Job.finished_at >= day_start,
                        Job.status.in_(("completed", "stopped")),
                    )
                )
            ).scalar_one()
            or 0
        )
        jobs_finished_today = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Job)
                    .where(Job.finished_at >= day_start, Job.status.in_(("completed", "stopped", "failed")))
                )
            ).scalar_one()
            or 0
        )
    else:
        jobs_queued = await _count_jobs("queued", user.id)
        jobs_running = await _count_jobs("running", user.id)
        jobs_completed = await _count_jobs("completed", user.id)
        jobs_failed = await _count_jobs("failed", user.id)
        jobs_stopped = await _count_jobs("stopped", user.id)
        rows_today = int(
            (
                await db.execute(
                    select(func.coalesce(func.sum(Job.rows_saved), 0)).where(
                        Job.owner_id == user.id,
                        Job.finished_at >= day_start,
                        Job.status.in_(("completed", "stopped")),
                    )
                )
            ).scalar_one()
            or 0
        )
        jobs_finished_today = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Job)
                    .where(
                        Job.owner_id == user.id,
                        Job.finished_at >= day_start,
                        Job.status.in_(("completed", "stopped", "failed")),
                    )
                )
            ).scalar_one()
            or 0
        )

    async def _user_job_stats(owner_id: int) -> tuple[int, int, int, int, int]:
        row = (
            await db.execute(
                select(
                    func.coalesce(func.sum(case((Job.status == "queued", 1), else_=0)), 0),
                    func.coalesce(func.sum(case((Job.status == "running", 1), else_=0)), 0),
                    func.coalesce(func.sum(case((Job.status == "completed", 1), else_=0)), 0),
                    func.coalesce(func.sum(Job.rows_saved), 0),
                    func.coalesce(
                        func.sum(case((Job.finished_at >= day_start, Job.rows_saved), else_=0)),
                        0,
                    ),
                ).where(Job.owner_id == owner_id)
            )
        ).one()
        return tuple(int(x or 0) for x in row)  # type: ignore[return-value]

    async def _active_sub(uid: int) -> Subscription | None:
        return (
            await db.execute(
                select(Subscription)
                .where(
                    Subscription.user_id == uid,
                    Subscription.is_active == True,  # noqa: E712
                    Subscription.expires_at > now,
                )
                .order_by(Subscription.expires_at.desc())
            )
        ).scalars().first()

    def _user_row(u: User, stats: tuple[int, int, int, int, int], sub: Subscription | None) -> dict:
        return {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "is_active": u.is_active,
            "jobs_queued": stats[0],
            "jobs_running": stats[1],
            "jobs_completed": stats[2],
            "rows_saved_total": stats[3],
            "rows_saved_today": stats[4],
            "subscription": sub.package_name if sub else None,
            "subscription_days_left": round((sub.expires_at - now).total_seconds() / 86400, 2)
            if sub and sub.expires_at
            else None,
        }

    users_payload: list[dict] = []
    if user.role == "admin":
        users = (await db.execute(select(User).order_by(User.id))).scalars().all()
        for u in users:
            users_payload.append(_user_row(u, await _user_job_stats(u.id), await _active_sub(u.id)))
    else:
        users_payload.append(
            _user_row(user, await _user_job_stats(user.id), await _active_sub(user.id))
        )

    users_with_running = sum(1 for u in users_payload if u["jobs_running"] > 0)

    # Fleet host metrics are admin-only; users see job/subscription live stats.
    show_workers = user.role == "admin"
    workers_out = worker_payloads if show_workers else []

    return {
        "generated_at": now.isoformat(),
        "poll_hint_sec": 4,
        "overview": {
            "workers_total": len(worker_payloads) if show_workers else 0,
            "workers_online": len(online_workers) if show_workers else 0,
            "workers_busy": len(busy_workers) if show_workers else 0,
            "workers_offline": (len(worker_payloads) - len(online_workers)) if show_workers else 0,
            "avg_cpu": _avg("cpu_percent") if show_workers else 0.0,
            "avg_mem": _avg("mem_percent") if show_workers else 0.0,
            "avg_disk": _avg("disk_percent") if show_workers else 0.0,
            "active_leases": sum(w["active_leases"] for w in worker_payloads) if show_workers else 0,
            "jobs_queued": jobs_queued,
            "jobs_running": jobs_running,
            "jobs_completed": jobs_completed,
            "jobs_failed": jobs_failed,
            "jobs_stopped": jobs_stopped,
            "jobs_finished_today": jobs_finished_today,
            "rows_saved_today": rows_today,
            "users_total": len(users_payload) if user.role == "admin" else 1,
            "users_with_running_jobs": users_with_running,
        },
        "workers": workers_out,
        "users": users_payload,
        "scope": "admin" if user.role == "admin" else "self",
    }
