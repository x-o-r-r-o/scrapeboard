"""Live dashboard / fleet statistics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_ready_user
from app.core.database import get_db
from app.models import (
    BotCommand,
    BotSettings,
    BotWorkflow,
    Job,
    JobChunk,
    Order,
    Package,
    ProxyPool,
    Subscription,
    User,
    UserWorker,
    WorkerNode,
)
from app.services.captcha_settings import captcha_dict_from, captcha_is_configured, get_captcha_settings
from app.services.jobs import job_thread_count
from app.services.perms import DEFAULT_USER_PERMS, effective_perms

router = APIRouter(prefix="/stats", tags=["stats"])

ONLINE_WINDOW = timedelta(seconds=90)
RECENT_JOBS_LIMIT = 8


def _proxy_count(text: str) -> int:
    return sum(1 for line in (text or "").splitlines() if line.strip() and not line.strip().startswith("#"))


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
        "proxy_pool_id": w.proxy_pool_id,
        "has_proxy_pool": w.proxy_pool_id is not None,
    }


def _thread_allowance_for(user: User, sub: Subscription | None) -> int:
    perms = effective_perms(user)
    cap = int(perms.get("max_threads") or DEFAULT_USER_PERMS["max_threads"])
    if user.role == "admin":
        return max(1, cap)
    if sub:
        cap = min(cap, int(sub.threads or cap))
    return max(1, cap)


@router.get("/live")
async def live_stats(user: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    is_admin = user.role == "admin"

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
    enabled_workers = [w for w in workers if w.is_enabled]
    capacity_total = sum(int(w.max_browsers or 0) for w in enabled_workers)
    capacity_used = sum(w["active_leases"] for w in worker_payloads)
    workers_draining = sum(1 for w in workers if w.is_enabled and w.is_draining)
    workers_disabled = sum(1 for w in workers if not w.is_enabled)

    def _avg(key: str) -> float:
        if not online_workers:
            return 0.0
        return round(sum(float(w[key]) for w in online_workers) / len(online_workers), 1)

    # --- Job status counts (scoped) ---
    async def _count_jobs(status: str | None = None, owner_id: int | None = None) -> int:
        q = select(func.count()).select_from(Job)
        if status:
            q = q.where(Job.status == status)
        if owner_id is not None:
            q = q.where(Job.owner_id == owner_id)
        return int((await db.execute(q)).scalar_one() or 0)

    owner_filter = None if is_admin else user.id
    jobs_queued = await _count_jobs("queued", owner_filter)
    jobs_running = await _count_jobs("running", owner_filter)
    jobs_completed = await _count_jobs("completed", owner_filter)
    jobs_failed = await _count_jobs("failed", owner_filter)
    jobs_stopped = await _count_jobs("stopped", owner_filter)

    rows_q = select(func.coalesce(func.sum(Job.rows_saved), 0)).where(
        Job.finished_at >= day_start,
        Job.status.in_(("completed", "stopped")),
    )
    finished_q = (
        select(func.count())
        .select_from(Job)
        .where(Job.finished_at >= day_start, Job.status.in_(("completed", "stopped", "failed")))
    )
    if owner_filter is not None:
        rows_q = rows_q.where(Job.owner_id == owner_filter)
        finished_q = finished_q.where(Job.owner_id == owner_filter)
    rows_today = int((await db.execute(rows_q)).scalar_one() or 0)
    jobs_finished_today = int((await db.execute(finished_q)).scalar_one() or 0)

    # --- Chunk aggregates (scoped via job ownership for non-admin) ---
    chunk_q = select(JobChunk.state, func.count()).group_by(JobChunk.state)
    if not is_admin:
        chunk_q = (
            select(JobChunk.state, func.count())
            .join(Job, Job.id == JobChunk.job_id)
            .where(Job.owner_id == user.id)
            .group_by(JobChunk.state)
        )
    chunk_rows = (await db.execute(chunk_q)).all()
    chunks_by_state = {str(state): int(cnt) for state, cnt in chunk_rows}
    chunks_pending = chunks_by_state.get("pending", 0)
    chunks_leased = chunks_by_state.get("leased", 0)
    chunks_done = chunks_by_state.get("done", 0)

    # --- Users + per-user job / thread stats (batched) ---
    if is_admin:
        users = (await db.execute(select(User).order_by(User.id))).scalars().all()
    else:
        users = [user]
    user_ids = [u.id for u in users]

    job_agg_rows = (
        await db.execute(
            select(
                Job.owner_id,
                func.coalesce(func.sum(case((Job.status == "queued", 1), else_=0)), 0),
                func.coalesce(func.sum(case((Job.status == "running", 1), else_=0)), 0),
                func.coalesce(func.sum(case((Job.status == "completed", 1), else_=0)), 0),
                func.coalesce(func.sum(case((Job.status == "failed", 1), else_=0)), 0),
                func.coalesce(func.sum(case((Job.status == "stopped", 1), else_=0)), 0),
                func.coalesce(func.sum(Job.rows_saved), 0),
                func.coalesce(
                    func.sum(case((Job.finished_at >= day_start, Job.rows_saved), else_=0)),
                    0,
                ),
            )
            .where(Job.owner_id.in_(user_ids))
            .group_by(Job.owner_id)
        )
    ).all()
    job_stats_by_user = {
        int(owner_id): {
            "jobs_queued": int(q or 0),
            "jobs_running": int(r or 0),
            "jobs_completed": int(c or 0),
            "jobs_failed": int(f or 0),
            "jobs_stopped": int(s or 0),
            "rows_saved_total": int(total or 0),
            "rows_saved_today": int(today or 0),
        }
        for owner_id, q, r, c, f, s, total, today in job_agg_rows
    }

    running_jobs = (
        await db.execute(select(Job).where(Job.status == "running", Job.owner_id.in_(user_ids)))
    ).scalars().all()
    threads_in_use_by_user: dict[int, int] = {}
    for j in running_jobs:
        threads_in_use_by_user[j.owner_id] = threads_in_use_by_user.get(j.owner_id, 0) + job_thread_count(j)

    sub_rows = (
        await db.execute(
            select(Subscription)
            .where(
                Subscription.user_id.in_(user_ids),
                Subscription.is_active == True,  # noqa: E712
                Subscription.expires_at > now,
            )
            .order_by(Subscription.expires_at.desc())
        )
    ).scalars().all()
    active_sub_by_user: dict[int, Subscription] = {}
    for s in sub_rows:
        if s.user_id not in active_sub_by_user:
            active_sub_by_user[s.user_id] = s

    dedicated_rows = (
        await db.execute(
            select(UserWorker.user_id, func.count())
            .where(UserWorker.user_id.in_(user_ids))
            .group_by(UserWorker.user_id)
        )
    ).all()
    dedicated_by_user = {int(uid): int(cnt) for uid, cnt in dedicated_rows}

    users_payload: list[dict] = []
    for u in users:
        stats = job_stats_by_user.get(
            u.id,
            {
                "jobs_queued": 0,
                "jobs_running": 0,
                "jobs_completed": 0,
                "jobs_failed": 0,
                "jobs_stopped": 0,
                "rows_saved_total": 0,
                "rows_saved_today": 0,
            },
        )
        sub = active_sub_by_user.get(u.id)
        allowance = _thread_allowance_for(u, sub)
        used = threads_in_use_by_user.get(u.id, 0)
        users_payload.append(
            {
                "id": u.id,
                "username": u.username,
                "role": u.role,
                "is_active": u.is_active,
                "jobs_queued": stats["jobs_queued"],
                "jobs_running": stats["jobs_running"],
                "jobs_completed": stats["jobs_completed"],
                "jobs_failed": stats["jobs_failed"],
                "jobs_stopped": stats["jobs_stopped"],
                "rows_saved_total": stats["rows_saved_total"],
                "rows_saved_today": stats["rows_saved_today"],
                "subscription": sub.package_name if sub else None,
                "subscription_days_left": (
                    round((_aware(sub.expires_at) - now).total_seconds() / 86400, 2)
                    if sub and _aware(sub.expires_at)
                    else None
                ),
                "thread_allowance": allowance,
                "threads_in_use": used,
                "threads_free": max(0, allowance - used),
                "dedicated_worker_count": dedicated_by_user.get(u.id, 0),
            }
        )

    users_with_running = sum(1 for u in users_payload if u["jobs_running"] > 0)
    subscriptions_active = sum(1 for u in users_payload if u["subscription"])

    # Caller quota (always present for the requesting user)
    me_row = next((u for u in users_payload if u["id"] == user.id), None)
    if me_row:
        quota = {
            "thread_allowance": me_row["thread_allowance"],
            "threads_in_use": me_row["threads_in_use"],
            "threads_free": me_row["threads_free"],
        }
    else:
        quota = {"thread_allowance": 0, "threads_in_use": 0, "threads_free": 0}

    # --- Recent jobs ---
    recent_q = select(Job).order_by(Job.id.desc()).limit(RECENT_JOBS_LIMIT)
    if not is_admin:
        recent_q = recent_q.where(Job.owner_id == user.id)
    recent = (await db.execute(recent_q)).scalars().all()
    recent_owner_ids = {j.owner_id for j in recent}
    owner_names: dict[int, str] = {}
    if recent_owner_ids:
        for ou in (
            await db.execute(select(User).where(User.id.in_(recent_owner_ids)))
        ).scalars().all():
            owner_names[ou.id] = ou.username
    recent_ids = [j.id for j in recent]
    chunk_by_job: dict[int, dict[str, int]] = {}
    if recent_ids:
        for jid, state, cnt in (
            await db.execute(
                select(JobChunk.job_id, JobChunk.state, func.count())
                .where(JobChunk.job_id.in_(recent_ids))
                .group_by(JobChunk.job_id, JobChunk.state)
            )
        ).all():
            bucket = chunk_by_job.setdefault(int(jid), {"pending": 0, "leased": 0, "done": 0})
            bucket[str(state)] = int(cnt)
    recent_jobs = [
        {
            "id": j.id,
            "public_id": j.public_id,
            "owner_id": j.owner_id,
            "owner_username": owner_names.get(j.owner_id),
            "status": j.status,
            "rows_saved": int(j.rows_saved or 0),
            "total_searches": int(j.total_searches or 0),
            "done_searches": int(j.done_searches or 0),
            "error": (j.error[:120] if j.error else None),
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "finished_at": j.finished_at.isoformat() if j.finished_at else None,
            "chunks_pending": chunk_by_job.get(j.id, {}).get("pending", 0),
            "chunks_leased": chunk_by_job.get(j.id, {}).get("leased", 0),
            "chunks_done": chunk_by_job.get(j.id, {}).get("done", 0),
        }
        for j in recent
    ]

    # --- Admin system snapshot (cheap DB counts only; no filesystem) ---
    system: dict | None = None
    if is_admin:
        packages = (await db.execute(select(Package))).scalars().all()
        pools = (await db.execute(select(ProxyPool))).scalars().all()
        captcha = await get_captcha_settings(db)
        captcha_d = captcha_dict_from(captcha)
        bot = await db.get(BotSettings, 1)
        bot_commands = int(
            (await db.execute(select(func.count()).select_from(BotCommand))).scalar_one() or 0
        )
        bot_workflows = int(
            (await db.execute(select(func.count()).select_from(BotWorkflow))).scalar_one() or 0
        )
        orders_pending = int(
            (
                await db.execute(
                    select(func.count()).select_from(Order).where(Order.status.in_(("pending", "paid")))
                )
            ).scalar_one()
            or 0
        )
        # Global active subs (all users), not just payload scope
        subs_active_global = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Subscription)
                    .where(
                        Subscription.is_active == True,  # noqa: E712
                        Subscription.expires_at > now,
                    )
                )
            ).scalar_one()
            or 0
        )
        users_active = int(
            (
                await db.execute(
                    select(func.count()).select_from(User).where(User.is_active == True)  # noqa: E712
                )
            ).scalar_one()
            or 0
        )
        users_with_dedicated = int(
            (
                await db.execute(select(func.count(func.distinct(UserWorker.user_id))))
            ).scalar_one()
            or 0
        )
        system = {
            "packages_total": len(packages),
            "packages_active": sum(1 for p in packages if p.is_active),
            "packages_dedicated": sum(1 for p in packages if p.dedicated_worker),
            "subscriptions_active": subs_active_global,
            "orders_pending": orders_pending,
            "users_total": len(users_payload),
            "users_active": users_active,
            "users_with_dedicated_workers": users_with_dedicated,
            "proxy_pools_total": len(pools),
            "proxy_pools_active": sum(1 for p in pools if p.is_active),
            "proxies_total": sum(_proxy_count(p.proxies_text) for p in pools if p.is_active),
            "workers_without_pool": sum(1 for w in workers if w.is_enabled and w.proxy_pool_id is None),
            "captcha_configured": captcha_is_configured(captcha),
            "captcha_provider": captcha_d["captcha_provider"]
            if captcha_d["captcha_provider"] != "none" or captcha_d["captcha_key"].strip()
            else "none",
            "captcha_backup_provider": captcha_d["captcha_backup_provider"]
            if captcha_d["captcha_backup_provider"] != "none" or captcha_d["captcha_backup_key"].strip()
            else "none",
            "bot_enabled": bool(bot.enabled) if bot else False,
            "bot_username": (bot.username or "") if bot else "",
            "bot_token_configured": bool(bot and (bot.token or "").strip()),
            "bot_commands": bot_commands,
            "bot_workflows": bot_workflows,
        }

    show_workers = is_admin
    workers_out = worker_payloads if show_workers else []

    return {
        "generated_at": now.isoformat(),
        "poll_hint_sec": 4,
        "scope": "admin" if is_admin else "self",
        "quota": quota,
        "overview": {
            "workers_total": len(worker_payloads) if show_workers else 0,
            "workers_online": len(online_workers) if show_workers else 0,
            "workers_busy": len(busy_workers) if show_workers else 0,
            "workers_offline": (len(worker_payloads) - len(online_workers)) if show_workers else 0,
            "workers_draining": workers_draining if show_workers else 0,
            "workers_disabled": workers_disabled if show_workers else 0,
            "capacity_total": capacity_total if show_workers else 0,
            "capacity_used": capacity_used if show_workers else 0,
            "avg_cpu": _avg("cpu_percent") if show_workers else 0.0,
            "avg_mem": _avg("mem_percent") if show_workers else 0.0,
            "avg_disk": _avg("disk_percent") if show_workers else 0.0,
            "active_leases": capacity_used if show_workers else 0,
            "jobs_queued": jobs_queued,
            "jobs_running": jobs_running,
            "jobs_completed": jobs_completed,
            "jobs_failed": jobs_failed,
            "jobs_stopped": jobs_stopped,
            "jobs_finished_today": jobs_finished_today,
            "rows_saved_today": rows_today,
            "chunks_pending": chunks_pending,
            "chunks_leased": chunks_leased,
            "chunks_done": chunks_done,
            "users_total": len(users_payload) if is_admin else 1,
            "users_with_running_jobs": users_with_running,
            "subscriptions_active": subscriptions_active if is_admin else (1 if me_row and me_row["subscription"] else 0),
        },
        "system": system,
        "recent_jobs": recent_jobs,
        "workers": workers_out,
        "users": users_payload,
    }
