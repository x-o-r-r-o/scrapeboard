from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.api.deps import require_admin, require_ready_user
from app.core.database import get_db
from app.core.config import get_settings
from app.core.security import (
    generate_worker_token,
    hash_password,
    hash_worker_token,
    verify_password,
)
from app.models import Job, JobChunk, Package, ProxyPool, User, UserWorker, WorkerNode
from app.schemas import (
    ProxyPoolAssign,
    ProxyPoolCreate,
    ProxyPoolOut,
    ProxyPoolUpdate,
    WorkerCreate,
    WorkerCreateResponse,
    WorkerFleetUpdateRequest,
    WorkerFleetUpdateResponse,
    WorkerOut,
    WorkerUpdate,
    WorkerUpdateStatusIn,
    WorkerUpdateStatusOut,
)
from app.services import jobs as jobs_svc
from app.services.billing import package_for_user, user_has_dedicated_worker
from app.services.notify import notify_user_telegram
from app.services.safe_zip import UnsafeArchiveError, safe_extract_csv_zip
from app.services.scrape_profiles import ensure_workers_have_default_profile
from app.services.captcha_settings import get_captcha_settings
from app.services.worker_config import (
    DEFAULT_WORKER_CONFIG,
    apply_worker_config_update,
    merge_lease_settings,
    normalize_worker_config,
    package_defaults_from_package,
    public_worker_config,
)
from app.services.worker_update import (
    apply_worker_status_report,
    attach_update_commands,
    clear_update,
    get_update_state,
    reconcile_update_state,
    request_update,
)

router = APIRouter(tags=["infra"])


def _proxy_count(text: str) -> int:
    return sum(1 for line in (text or "").splitlines() if line.strip() and not line.strip().startswith("#"))


async def _pool_out(db: AsyncSession, p: ProxyPool) -> ProxyPoolOut:
    workers = (
        await db.execute(select(WorkerNode).where(WorkerNode.proxy_pool_id == p.id).order_by(WorkerNode.id))
    ).scalars().all()
    return ProxyPoolOut(
        id=p.id,
        name=p.name,
        description=p.description or "",
        proxy_count=_proxy_count(p.proxies_text),
        is_active=p.is_active,
        worker_ids=[w.id for w in workers],
        worker_names=[w.name for w in workers],
    )


def _worker_out(
    w: WorkerNode,
    *,
    active_leases: int = 0,
    pool_name: str | None = None,
) -> WorkerOut:
    online = False
    if w.last_seen_at:
        ts = w.last_seen_at if w.last_seen_at.tzinfo else w.last_seen_at.replace(tzinfo=timezone.utc)
        online = datetime.now(timezone.utc) - ts < timedelta(seconds=90)
    raw = w.worker_config or {}
    if not raw:
        raw = dict(DEFAULT_WORKER_CONFIG)
    return WorkerOut(
        id=w.id,
        name=w.name,
        token_prefix=w.token_prefix,
        is_enabled=w.is_enabled,
        is_draining=w.is_draining,
        max_browsers=w.max_browsers,
        proxy_pool_id=w.proxy_pool_id,
        proxy_pool_name=pool_name,
        last_seen_at=w.last_seen_at,
        cpu_percent=float(w.cpu_percent or 0),
        mem_percent=float(w.mem_percent or 0),
        disk_percent=float(getattr(w, "disk_percent", 0) or 0),
        mem_used_gb=float(getattr(w, "mem_used_gb", 0) or 0),
        mem_total_gb=float(getattr(w, "mem_total_gb", 0) or 0),
        disk_used_gb=float(getattr(w, "disk_used_gb", 0) or 0),
        disk_total_gb=float(getattr(w, "disk_total_gb", 0) or 0),
        load_avg_1=float(getattr(w, "load_avg_1", 0) or 0),
        load_avg_5=float(getattr(w, "load_avg_5", 0) or 0),
        load_avg_15=float(getattr(w, "load_avg_15", 0) or 0),
        host_os=str(getattr(w, "host_os", "") or ""),
        hostname=str(getattr(w, "hostname", "") or ""),
        version=w.version or "",
        online=online,
        active_leases=active_leases,
        worker_config=public_worker_config(raw),
        update=WorkerUpdateStatusOut(**get_update_state(w)),
    )


def _redact_worker_config(cfg: dict) -> dict:
    """Heartbeat-safe config: never send captcha keys over the wire on heartbeat."""
    out = dict(cfg or {})
    if out.get("captcha_key"):
        out["captcha_key_configured"] = True
    else:
        out["captcha_key_configured"] = bool(out.get("captcha_key_configured"))
    if out.get("captcha_backup_key"):
        out["captcha_backup_key_configured"] = True
    else:
        out["captcha_backup_key_configured"] = bool(out.get("captcha_backup_key_configured"))
    out.pop("captcha_key", None)
    out.pop("captcha_backup_key", None)
    return out


def _set_worker_token(w: WorkerNode, raw: str) -> None:
    w.token_hash = hash_password(raw)
    w.token_prefix = raw[:8]
    w.token_lookup = hash_worker_token(raw)


def _install_hint(token: str) -> str:
    return (
        "python agent.py --setup\n"
        "# or:\n"
        f"python agent.py --panel-url https://scrape.cvmso.com --token {token}\n"
        "\n"
        "# After config exists, install as a background service (starts at login):\n"
        "#   macOS/Linux:  bash install_service.sh\n"
        "#   Windows:      install_service.bat"
    )


# --- proxy pools ---

@router.get("/proxy-pools", response_model=list[ProxyPoolOut])
async def list_pools(_: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ProxyPool).order_by(ProxyPool.id))).scalars().all()
    return [await _pool_out(db, p) for p in rows]


@router.post("/proxy-pools", response_model=ProxyPoolOut)
async def create_pool(body: ProxyPoolCreate, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    p = ProxyPool(**body.model_dump())
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return await _pool_out(db, p)


@router.get("/proxy-pools/{pool_id}")
async def get_pool(pool_id: int, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    p = await db.get(ProxyPool, pool_id)
    if not p:
        raise HTTPException(404, "Not found")
    out = await _pool_out(db, p)
    return {**out.model_dump(), "proxies_text": p.proxies_text}


@router.patch("/proxy-pools/{pool_id}", response_model=ProxyPoolOut)
async def update_pool(pool_id: int, body: ProxyPoolUpdate, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    p = await db.get(ProxyPool, pool_id)
    if not p:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    await db.commit()
    await db.refresh(p)
    return await _pool_out(db, p)


@router.post("/proxy-pools/{pool_id}/assign", response_model=ProxyPoolOut)
async def assign_pool_workers(
    pool_id: int,
    body: ProxyPoolAssign,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    p = await db.get(ProxyPool, pool_id)
    if not p:
        raise HTTPException(404, "Not found")
    wanted = set(int(x) for x in body.worker_ids)
    # Clear workers currently on this pool but not in the new set
    current = (
        await db.execute(select(WorkerNode).where(WorkerNode.proxy_pool_id == pool_id))
    ).scalars().all()
    for w in current:
        if w.id not in wanted:
            w.proxy_pool_id = None
    # Assign requested workers
    if wanted:
        rows = (
            await db.execute(select(WorkerNode).where(WorkerNode.id.in_(wanted)))
        ).scalars().all()
        found = {w.id for w in rows}
        missing = wanted - found
        if missing:
            raise HTTPException(404, f"Workers not found: {sorted(missing)}")
        for w in rows:
            w.proxy_pool_id = pool_id
    await db.commit()
    await db.refresh(p)
    return await _pool_out(db, p)


@router.delete("/proxy-pools/{pool_id}")
async def delete_pool(pool_id: int, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    p = await db.get(ProxyPool, pool_id)
    if not p:
        raise HTTPException(404, "Not found")
    workers = (
        await db.execute(select(WorkerNode).where(WorkerNode.proxy_pool_id == pool_id))
    ).scalars().all()
    for w in workers:
        w.proxy_pool_id = None
    await db.delete(p)
    await db.commit()
    return {"detail": "Deleted"}


# --- workers ---

async def _enrich_worker(
    db: AsyncSession,
    w: WorkerNode,
    leases: dict[int, int] | None = None,
    *,
    reconcile: bool = True,
) -> WorkerOut:
    if reconcile:
        before = get_update_state(w)
        after = reconcile_update_state(w)
        if before.get("status") != after.get("status") or before.get("message") != after.get("message"):
            await db.commit()
    pool_name = None
    if w.proxy_pool_id:
        pool = await db.get(ProxyPool, w.proxy_pool_id)
        pool_name = pool.name if pool else None
    return _worker_out(
        w,
        active_leases=(leases or {}).get(w.id, 0),
        pool_name=pool_name,
    )


async def _seed_worker_config(
    db: AsyncSession,
    *,
    seed_from_package_id: int | None,
    patch: dict | None = None,
) -> dict:
    """Build worker_config: package scrape_defaults (optional) → built-in → patch."""
    if seed_from_package_id:
        pkg = await db.get(Package, seed_from_package_id)
        if not pkg:
            raise HTTPException(404, "Package not found for seed_from_package_id")
        cfg = package_defaults_from_package(pkg)
    else:
        cfg = dict(DEFAULT_WORKER_CONFIG)
    if patch:
        cfg = apply_worker_config_update(cfg, patch)
    return normalize_worker_config(cfg)


@router.get("/workers", response_model=list[WorkerOut])
async def list_workers(_: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    await ensure_workers_have_default_profile(db)
    rows = (await db.execute(select(WorkerNode).order_by(WorkerNode.id))).scalars().all()
    lease_rows = (
        await db.execute(
            select(JobChunk.worker_id, func.count())
            .where(JobChunk.state == "leased", JobChunk.worker_id.is_not(None))
            .group_by(JobChunk.worker_id)
        )
    ).all()
    leases = {int(wid): int(cnt) for wid, cnt in lease_rows if wid is not None}
    return [await _enrich_worker(db, w, leases) for w in rows]


@router.post("/workers", response_model=WorkerCreateResponse)
async def create_worker(body: WorkerCreate, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    raw = generate_worker_token()
    patch = body.worker_config.model_dump(exclude_unset=True) if body.worker_config is not None else None
    cfg = await _seed_worker_config(
        db,
        seed_from_package_id=body.seed_from_package_id,
        patch=patch,
    )
    w = WorkerNode(
        name=body.name,
        max_browsers=body.max_browsers,
        proxy_pool_id=body.proxy_pool_id,
        worker_config=cfg,
    )
    _set_worker_token(w, raw)
    db.add(w)
    await db.commit()
    await db.refresh(w)
    return WorkerCreateResponse(
        worker=await _enrich_worker(db, w),
        token=raw,
        install_hint=_install_hint(raw),
    )


@router.patch("/workers/{worker_id}", response_model=WorkerOut)
async def update_worker(worker_id: int, body: WorkerUpdate, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    w = await db.get(WorkerNode, worker_id)
    if not w:
        raise HTTPException(404, "Not found")
    data = body.model_dump(exclude_unset=True)
    reset = data.pop("reset_config_to_defaults", False)
    seed_pkg = data.pop("seed_from_package_id", None)
    cfg_patch = data.pop("worker_config", None)
    for k, v in data.items():
        setattr(w, k, v)
    if reset or seed_pkg is not None:
        w.worker_config = await _seed_worker_config(
            db,
            seed_from_package_id=seed_pkg,
            patch=None,
        )
    elif cfg_patch is not None:
        w.worker_config = apply_worker_config_update(w.worker_config or {}, cfg_patch)
    await db.commit()
    await db.refresh(w)
    return await _enrich_worker(db, w)


@router.post("/workers/{worker_id}/rotate-token", response_model=WorkerCreateResponse)
async def rotate_token(worker_id: int, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    w = await db.get(WorkerNode, worker_id)
    if not w:
        raise HTTPException(404, "Not found")
    raw = generate_worker_token()
    _set_worker_token(w, raw)
    await db.commit()
    await db.refresh(w)
    return WorkerCreateResponse(
        worker=await _enrich_worker(db, w),
        token=raw,
        install_hint=_install_hint(raw),
    )


@router.post("/workers/request-update", response_model=WorkerFleetUpdateResponse)
async def request_workers_update(
    body: WorkerFleetUpdateRequest,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    """Queue a git-based worker update for all (or selected) workers.

    Online agents pick this up on the next heartbeat and run the fixed
    worker update path (install.py --role worker --update). No arbitrary shell.
    """
    q = select(WorkerNode).order_by(WorkerNode.id)
    if body.worker_ids:
        q = q.where(WorkerNode.id.in_(body.worker_ids))
    rows = (await db.execute(q)).scalars().all()
    if body.worker_ids and not rows:
        raise HTTPException(404, "No matching workers")
    ref = body.ref
    for w in rows:
        request_update(w, ref=ref)
    await db.commit()
    for w in rows:
        await db.refresh(w)
    enriched = [await _enrich_worker(db, w, reconcile=False) for w in rows]
    pending_n = sum(1 for e in enriched if e.update.status == "pending")
    return WorkerFleetUpdateResponse(
        ok=True,
        ref=enriched[0].update.ref if enriched else (body.ref or "main"),
        queued=pending_n,
        workers=enriched,
    )


@router.post("/workers/{worker_id}/request-update", response_model=WorkerOut)
async def request_one_worker_update(
    worker_id: int,
    body: WorkerFleetUpdateRequest = WorkerFleetUpdateRequest(),
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    w = await db.get(WorkerNode, worker_id)
    if not w:
        raise HTTPException(404, "Not found")
    request_update(w, ref=body.ref)
    await db.commit()
    await db.refresh(w)
    return await _enrich_worker(db, w, reconcile=False)


@router.post("/workers/{worker_id}/clear-update", response_model=WorkerOut)
async def clear_worker_update(
    worker_id: int,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin: clear stuck pending/failed update state (e.g. after manual restart)."""
    w = await db.get(WorkerNode, worker_id)
    if not w:
        raise HTTPException(404, "Not found")
    clear_update(w)
    await db.commit()
    await db.refresh(w)
    return await _enrich_worker(db, w, reconcile=False)


# --- worker agent protocol ---

async def _auth_worker(db: AsyncSession, authorization: str | None) -> WorkerNode:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Worker token required")
    token = authorization.removeprefix("Bearer ").strip()
    if not token or len(token) < 16:
        raise HTTPException(403, "Invalid worker token")

    lookup = hash_worker_token(token)
    w = (
        await db.execute(select(WorkerNode).where(WorkerNode.token_lookup == lookup))
    ).scalar_one_or_none()
    if w and verify_password(token, w.token_hash):
        return w

    # Legacy workers created before token_lookup: one-time bcrypt scan + backfill
    legacy = (
        await db.execute(
            select(WorkerNode).where(
                (WorkerNode.token_lookup == None) | (WorkerNode.token_lookup == "")  # noqa: E711
            )
        )
    ).scalars().all()
    for cand in legacy:
        if verify_password(token, cand.token_hash):
            cand.token_lookup = lookup
            await db.commit()
            return cand
    raise HTTPException(403, "Invalid worker token")


def _require_active_worker(w: WorkerNode) -> None:
    if not w.is_enabled:
        raise HTTPException(403, "Worker is disabled")


def _body_int(body: dict, key: str, *, default: int | None = None, required: bool = False) -> int:
    """Parse an int from JSON without treating 0 as missing (``x or default`` is wrong)."""
    raw = body.get(key) if key in body else None
    if raw is None or raw == "":
        if required or default is None:
            raise HTTPException(400, f"{key} required")
        return default
    try:
        return int(body[key])
    except (TypeError, ValueError) as e:
        raise HTTPException(400, f"{key} must be an integer") from e


async def _require_chunk_lease(db: AsyncSession, w: WorkerNode, job: Job, chunk_id: int) -> JobChunk:
    chunk = (
        await db.execute(
            select(JobChunk).where(JobChunk.job_id == job.id, JobChunk.chunk_id == chunk_id)
        )
    ).scalar_one_or_none()
    if not chunk:
        raise HTTPException(404, "Chunk not found")
    if chunk.state not in ("leased", "done"):
        raise HTTPException(409, "Chunk is not leased to this worker")
    if chunk.worker_id != w.id:
        raise HTTPException(403, "Chunk leased to another worker")
    return chunk


async def _clear_worker_leases_for_job(
    db: AsyncSession,
    w: WorkerNode,
    job: Job,
    *,
    chunk_id: int | None = None,
    rows: int = 0,
    sync_progress: bool = False,
) -> int:
    """Mark this worker's leased chunks on the job done (frees active_leases).

    When ``sync_progress`` is True (running-job ack fallback), also recompute
    ``job.done_searches`` from done chunk ranges so progress cannot stay at 0
    after chunks are marked done.
    """
    q = select(JobChunk).where(
        JobChunk.job_id == job.id,
        JobChunk.worker_id == w.id,
        JobChunk.state == "leased",
    )
    if chunk_id is not None:
        q = q.where(JobChunk.chunk_id == chunk_id)
    chunks = (await db.execute(q)).scalars().all()
    for chunk in chunks:
        chunk.state = "done"
        if rows and not chunk.rows:
            chunk.rows = rows
            job.rows_saved += rows
        chunk.leased_at = None
    if sync_progress and chunks:
        await jobs_svc.sync_job_done_searches(db, job)
    return len(chunks)


def _lease_is_stale(leased_at: datetime | None, before: datetime) -> bool:
    if not leased_at:
        return True
    ts = leased_at if leased_at.tzinfo else leased_at.replace(tzinfo=timezone.utc)
    return ts < before


async def _reclaim_stale_leases(
    db: AsyncSession,
    *,
    worker_id: int | None = None,
    job_id: int | None = None,
    stale_before: datetime | None = None,
) -> int:
    """Return leased chunks older than TTL to pending (capacity recovery)."""
    before = stale_before or (datetime.now(timezone.utc) - timedelta(seconds=120))
    clauses = [JobChunk.state == "leased"]
    if worker_id is not None and job_id is not None:
        clauses.append(or_(JobChunk.job_id == job_id, JobChunk.worker_id == worker_id))
    elif worker_id is not None:
        clauses.append(JobChunk.worker_id == worker_id)
    elif job_id is not None:
        clauses.append(JobChunk.job_id == job_id)
    rows = (await db.execute(select(JobChunk).where(*clauses))).scalars().all()
    n = 0
    for c in rows:
        if _lease_is_stale(c.leased_at, before):
            c.state = "pending"
            c.worker_id = None
            c.leased_at = None
            n += 1
    return n


@router.post("/worker-api/hello")
async def worker_hello(
    body: dict | None = None,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """First-connect handshake: validates token and returns panel identity (no secrets)."""
    w = await _auth_worker(db, authorization)
    body = body or {}
    w.last_seen_at = datetime.now(timezone.utc)
    w.version = str(body.get("version") or w.version)
    if body.get("os") or body.get("host_os"):
        w.host_os = str(body.get("os") or body.get("host_os"))[:64]
    if body.get("hostname"):
        w.hostname = str(body.get("hostname"))[:128]
    await db.commit()
    return {
        "ok": True,
        "panel": "scrapeboard",
        "worker_id": w.id,
        "name": w.name,
        "enabled": w.is_enabled,
        "drain": w.is_draining,
        "max_browsers": w.max_browsers,
        "message": "Connected. Jobs only come from panel users / linked Telegram accounts.",
    }


_WORKER_LOG_MAX_LINES = 400
_WORKER_LOG_MAX_CHARS = 200_000


def _store_worker_log_lines(w: WorkerNode, lines: list[str], *, replace: bool = False) -> None:
    """Keep a bounded ring of recent log lines on WorkerNode.meta."""
    meta = dict(w.meta or {})
    cleaned: list[str] = []
    for raw in lines:
        s = str(raw).replace("\x00", "")
        if len(s) > 4000:
            s = s[:4000] + "…"
        cleaned.append(s)
    if replace:
        buf = cleaned
    else:
        prev = meta.get("log_lines")
        buf = list(prev) if isinstance(prev, list) else []
        buf.extend(cleaned)
    if len(buf) > _WORKER_LOG_MAX_LINES:
        buf = buf[-_WORKER_LOG_MAX_LINES:]
    # Soft char budget — drop from the head if needed
    total = sum(len(x) + 1 for x in buf)
    while buf and total > _WORKER_LOG_MAX_CHARS:
        total -= len(buf[0]) + 1
        buf.pop(0)
    meta["log_lines"] = buf
    meta["log_updated_at"] = datetime.now(timezone.utc).isoformat()
    w.meta = meta
    flag_modified(w, "meta")


@router.post("/worker-api/heartbeat")
async def worker_heartbeat(
    body: dict,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    w = await _auth_worker(db, authorization)
    now = datetime.now(timezone.utc)
    w.last_seen_at = now
    w.cpu_percent = float(body.get("cpu") or 0)
    w.mem_percent = float(body.get("mem") or 0)
    w.disk_percent = float(body.get("disk") or body.get("disk_percent") or 0)
    w.mem_used_gb = float(body.get("mem_used_gb") or 0)
    w.mem_total_gb = float(body.get("mem_total_gb") or 0)
    w.disk_used_gb = float(body.get("disk_used_gb") or 0)
    w.disk_total_gb = float(body.get("disk_total_gb") or 0)
    w.load_avg_1 = float(body.get("load_1") or body.get("load_avg_1") or 0)
    w.load_avg_5 = float(body.get("load_5") or body.get("load_avg_5") or 0)
    w.load_avg_15 = float(body.get("load_15") or body.get("load_avg_15") or 0)
    if body.get("hostname"):
        w.hostname = str(body.get("hostname"))[:128]
    if body.get("os") or body.get("host_os"):
        w.host_os = str(body.get("os") or body.get("host_os"))[:64]
    w.version = str(body.get("version") or w.version)

    # Refresh lease TTL + surface jobs that are no longer active so the agent can stop.
    # When the agent reports active_chunks, only refresh those; reclaim the rest so a
    # failed ack cannot leave active_leases stuck (heartbeat used to refresh zombies forever).
    cancel_jobs: list[str] = []
    leased = (
        await db.execute(
            select(JobChunk).where(JobChunk.worker_id == w.id, JobChunk.state == "leased")
        )
    ).scalars().all()
    reported_active: set[tuple[str, int]] | None = None
    if "active_chunks" in body:
        reported_active = set()
        raw_active = body.get("active_chunks") or []
        if isinstance(raw_active, list):
            for item in raw_active:
                if not isinstance(item, dict):
                    continue
                pid = str(item.get("job_id") or "").strip()
                if not pid or item.get("chunk_id") is None or item.get("chunk_id") == "":
                    continue
                try:
                    reported_active.add((pid, int(item["chunk_id"])))
                except (TypeError, ValueError):
                    continue

    if leased:
        job_ids = {c.job_id for c in leased}
        jobs = (
            await db.execute(select(Job).where(Job.id.in_(job_ids)))
        ).scalars().all()
        by_id = {j.id: j for j in jobs}
        for c in leased:
            j = by_id.get(c.job_id)
            public_id = j.public_id if j else ""
            terminal = bool(j and j.status not in ("running", "queued"))
            if terminal:
                cancel_jobs.append(public_id)
                # Drop capacity even if the agent ignores cancel_jobs (pre-0.8.0).
                c.state = "done"
                c.worker_id = None
                c.leased_at = None
                continue
            key = (public_id, c.chunk_id)
            if reported_active is not None and key not in reported_active:
                # Agent no longer running this instance — free the lease immediately.
                c.state = "pending"
                c.worker_id = None
                c.leased_at = None
                continue
            c.leased_at = now

    # TTL reclaim for this worker even when lease polls are failing (502).
    # With active_chunks, orphans are already cleared above; this covers legacy agents.
    if reported_active is None:
        await _reclaim_stale_leases(db, worker_id=w.id)

    # Also deliver cancel hints queued by finalize_job after leases were cleared.
    meta = dict(w.meta or {})
    queued = meta.get("cancel_jobs") or {}
    if isinstance(queued, dict) and queued:
        fresh: dict = {}
        for pid, seen_at in queued.items():
            try:
                ts = datetime.fromisoformat(str(seen_at).replace("Z", "+00:00"))
            except Exception:
                ts = now
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if (now - ts).total_seconds() < 600:
                cancel_jobs.append(str(pid))
                fresh[str(pid)] = seen_at
        meta["cancel_jobs"] = fresh
        w.meta = meta
        flag_modified(w, "meta")
    elif isinstance(queued, list):
        # Legacy list form
        cancel_jobs.extend(str(x) for x in queued)
        meta["cancel_jobs"] = {}
        w.meta = meta
        flag_modified(w, "meta")

    captcha = await get_captcha_settings(db)
    # Heartbeat sync uses worker machine config only (no job/package context yet)
    effective = merge_lease_settings(
        package_defaults=None,
        worker_config=w.worker_config or {},
        job_settings={},
        max_browsers=w.max_browsers,
        captcha=captcha,
    )
    out: dict = {
        "ok": True,
        "drain": w.is_draining,
        "enabled": w.is_enabled,
        "name": w.name,
        "max_browsers": w.max_browsers,
        "proxy_pool_id": w.proxy_pool_id,
        # Redacted: captcha keys only delivered inside job leases
        "worker_config": _redact_worker_config(effective),
        "cancel_jobs": sorted(set(cancel_jobs)),
        "commands": [],
    }
    # Pending updates + old-agent/stale reconcile (may mutate w.meta)
    attach_update_commands(out, w)
    await db.commit()
    return out


@router.post("/worker-api/update-status")
async def worker_update_status(
    body: WorkerUpdateStatusIn,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Worker reports progress of a panel-requested git update."""
    w = await _auth_worker(db, authorization)
    w.last_seen_at = datetime.now(timezone.utc)
    state = apply_worker_status_report(
        w,
        status=body.status,
        message=body.message or "",
        ref=body.ref,
    )
    await db.commit()
    return {"ok": True, "update": state}


@router.post("/worker-api/logs")
async def worker_push_logs(
    body: dict,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Worker pushes recent log lines (ring buffer stored on WorkerNode.meta)."""
    w = await _auth_worker(db, authorization)
    w.last_seen_at = datetime.now(timezone.utc)
    raw_lines = body.get("lines")
    if not isinstance(raw_lines, list):
        raise HTTPException(400, "lines must be a list of strings")
    # Cap inbound batch size
    lines = [str(x) for x in raw_lines[-_WORKER_LOG_MAX_LINES:]]
    replace = bool(body.get("replace"))
    _store_worker_log_lines(w, lines, replace=replace)
    await db.commit()
    meta = w.meta or {}
    return {
        "ok": True,
        "stored": len(meta.get("log_lines") or []),
        "updated_at": meta.get("log_updated_at"),
    }


@router.get("/workers/{worker_id}/logs")
async def get_worker_logs(
    worker_id: int,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only live tail of recent worker log lines."""
    w = await db.get(WorkerNode, worker_id)
    if not w:
        raise HTTPException(404, "Not found")
    meta = w.meta or {}
    lines = meta.get("log_lines") if isinstance(meta.get("log_lines"), list) else []
    return {
        "worker_id": w.id,
        "name": w.name,
        "lines": lines,
        "updated_at": meta.get("log_updated_at"),
        "online": bool(w.last_seen_at and (datetime.now(timezone.utc) - (
            w.last_seen_at if w.last_seen_at.tzinfo else w.last_seen_at.replace(tzinfo=timezone.utc)
        )).total_seconds() < 90),
    }


@router.post("/worker-api/lease")
async def worker_lease(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    w = await _auth_worker(db, authorization)

    async def _with_update(payload: dict) -> dict:
        """Deliver pending update on lease polls too (not only heartbeat)."""
        attach_update_commands(payload, w)
        await db.commit()
        return payload

    if w.is_draining or not w.is_enabled:
        return await _with_update({"chunk": None})

    # max_browsers = concurrent user-job instances (leases) this worker may hold
    active_leases = int(
        (
            await db.execute(
                select(func.count())
                .select_from(JobChunk)
                .where(JobChunk.worker_id == w.id, JobChunk.state == "leased")
            )
        ).scalar_one()
        or 0
    )
    slot_cap = max(1, int(w.max_browsers or 1))
    if active_leases >= slot_cap:
        return await _with_update(
            {"chunk": None, "slots_full": True, "active_leases": active_leases, "max_browsers": slot_cap}
        )

    # Prefer spreading across users: owners already active on this worker go last
    busy_owners = set(
        (
            await db.execute(
                select(Job.owner_id)
                .join(JobChunk, JobChunk.job_id == Job.id)
                .where(JobChunk.worker_id == w.id, JobChunk.state == "leased")
            )
        ).scalars().all()
    )

    async def _owner_allows(owner_id: int) -> bool:
        """Shared-pool vs pin rule for leasing this worker.

        - No UserWorker rows → any enabled worker (shared pool / round-robin).
        - Pinning applies ONLY when the owner has a dedicated_worker package
          AND one or more workers assigned.
        - dedicated_worker package but empty assignment → still any worker.
        - Without dedicated_worker → ignore UserWorker rows (shared pool).
        """
        owner = await db.get(User, owner_id)
        if not owner:
            return False
        if not await user_has_dedicated_worker(db, owner):
            return True  # shared pool: UserWorker rows (if any) are ignored
        allowed = (
            await db.execute(select(UserWorker.worker_id).where(UserWorker.user_id == owner_id))
        ).scalars().all()
        if not allowed:
            return True  # dedicated but unassigned → optional pin; shared pool OK
        return w.id in {int(x) for x in allowed}

    async def _has_pending(job_id: int) -> bool:
        row = (
            await db.execute(
                select(JobChunk.id).where(JobChunk.job_id == job_id, JobChunk.state == "pending").limit(1)
            )
        ).scalars().first()
        return row is not None

    async def _next_job(*, skip_ids: set[int] | None = None) -> Job | None:
        skip = skip_ids or set()
        primary: list[Job] = []
        secondary: list[Job] = []
        for status in ("running", "queued"):
            candidates = (
                await db.execute(select(Job).where(Job.status == status).order_by(Job.id))
            ).scalars().all()
            for cand in candidates:
                if cand.id in skip:
                    continue
                if not await _owner_allows(cand.owner_id):
                    continue
                if status == "running" and not await _has_pending(cand.id):
                    continue
                # Shared per-user thread quota: queued jobs wait until free threads cover them
                if status == "queued" and not await jobs_svc.can_start_job(db, cand):
                    continue
                if cand.owner_id in busy_owners:
                    secondary.append(cand)
                else:
                    primary.append(cand)
        for cand in primary + secondary:
            # Do not promote queued→running here — only after a chunk is claimed,
            # so thread quota is not held without work.
            return cand
        return None

    job = await _next_job()
    if not job:
        return await _with_update({"chunk": None})

    # reclaim stale leases (global for this worker + current job)
    await _reclaim_stale_leases(db, worker_id=w.id, job_id=job.id)
    await db.commit()

    # Re-check capacity after reclaim
    active_leases = int(
        (
            await db.execute(
                select(func.count())
                .select_from(JobChunk)
                .where(JobChunk.worker_id == w.id, JobChunk.state == "leased")
            )
        ).scalar_one()
        or 0
    )
    if active_leases >= slot_cap:
        return await _with_update(
            {"chunk": None, "slots_full": True, "active_leases": active_leases, "max_browsers": slot_cap}
        )

    await db.refresh(job)
    if job.status == "stopped":
        return await _with_update({"chunk": None})
    # Queued jobs must still fit thread quota at claim time
    if job.status == "queued" and not await jobs_svc.can_start_job(db, job):
        return await _with_update({"chunk": None})

    # Atomic claim of one pending chunk. Different workers may hold different
    # chunks of the same job at once — there is no global "one lease per job" lock.
    # Only the pending→leased CAS below serializes contention on a single chunk row.
    claimed: JobChunk | None = None
    tried: set[int] = set()
    for _ in range(8):
        if job.status == "queued" and not await jobs_svc.can_start_job(db, job):
            tried.add(job.id)
            job = await _next_job(skip_ids=tried)
            if not job:
                return await _with_update({"chunk": None})
            continue
        candidate = (
            await db.execute(
                select(JobChunk)
                .where(JobChunk.job_id == job.id, JobChunk.state == "pending")
                .order_by(JobChunk.chunk_id)
            )
        ).scalars().first()
        if not candidate:
            tried.add(job.id)
            job = await _next_job(skip_ids=tried)
            if not job:
                return await _with_update({"chunk": None})
            continue
        now = datetime.now(timezone.utc)
        result = await db.execute(
            update(JobChunk)
            .where(JobChunk.id == candidate.id, JobChunk.state == "pending")
            .values(state="leased", worker_id=w.id, leased_at=now)
        )
        await db.commit()
        if result.rowcount == 1:
            await db.refresh(candidate)
            claimed = candidate
            break
        # Lost race on this chunk; loop and take the next pending chunk of this
        # job (or another job) so the fleet stays parallelized.
    if not claimed:
        return await _with_update({"chunk": None})

    # Promote queued → running only after a successful claim (thread quota consumed now)
    await db.refresh(job)
    if job.status == "queued":
        if not await jobs_svc.can_start_job(db, job):
            # Lost race on quota — release the chunk and leave job queued
            claimed.state = "pending"
            claimed.worker_id = None
            claimed.leased_at = None
            await db.commit()
            return await _with_update({"chunk": None})
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        settings = dict(job.settings or {})
        settings.pop("queued_for_threads", None)
        job.settings = settings
        await db.commit()
        await db.refresh(job)
        # Final oversubscribe check
        owner = await db.get(User, job.owner_id)
        if owner:
            used = await jobs_svc.sum_running_threads(db, job.owner_id)
            allowance = await jobs_svc.user_thread_allowance(db, owner)
            if used > allowance:
                job.status = "queued"
                job.started_at = None
                claimed.state = "pending"
                claimed.worker_id = None
                claimed.leased_at = None
                await db.commit()
                return await _with_update({"chunk": None})

    scrape = None  # legacy unused; package_defaults replace scrape profiles
    proxies_text = ""
    if w.proxy_pool_id:
        pool = await db.get(ProxyPool, w.proxy_pool_id)
        if pool and pool.is_active:
            proxies_text = pool.proxies_text

    owner = await db.get(User, job.owner_id)
    pkg = await package_for_user(db, owner) if owner else None
    package_defaults = package_defaults_from_package(pkg)

    captcha = await get_captcha_settings(db)
    settings = merge_lease_settings(
        package_defaults=package_defaults,
        scrape=scrape,
        worker_config=w.worker_config or {},
        job_settings=dict(job.settings or {}),
        max_browsers=w.max_browsers,
        captcha=captcha,
    )

    keywords, locations = [], []
    try:
        if job.keywords_path:
            with open(job.keywords_path, encoding="utf-8") as f:
                keywords = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        if job.locations_path:
            with open(job.locations_path, encoding="utf-8") as f:
                locations = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    except OSError:
        pass

    return await _with_update(
        {
            "chunk": {
                "id": claimed.chunk_id,
                "start": claimed.start_index,
                "end": claimed.end_index,
            },
            "job": {
                "job_id": job.public_id,
                "owner_id": job.owner_id,
                "keywords": keywords,
                "locations": locations,
                "settings": settings,
                "proxies_text": proxies_text,
                "ts": job.public_id,
            },
            "active_leases": active_leases + 1,
            "max_browsers": slot_cap,
        }
    )


@router.post("/worker-api/upload")
async def worker_upload(
    job_id: str,
    chunk_id: int,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    cfg = get_settings()
    w = await _auth_worker(db, authorization)
    _require_active_worker(w)
    job = (await db.execute(select(Job).where(Job.public_id == job_id))).scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")
    # Allow upload while this worker still holds the lease even if the job was
    # just stopped/completed (partial results); reject only when lease is gone.
    chunk = await _require_chunk_lease(db, w, job, chunk_id)
    if job.status not in ("running", "queued") and chunk.state != "leased":
        raise HTTPException(409, "Job is not active")

    dest_dir = jobs_svc.job_parts_dir(job.public_id, job.owner_id) / str(chunk_id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    max_bytes = int(cfg.worker_upload_max_bytes)
    raw = bytearray()
    while True:
        piece = await file.read(1024 * 1024)
        if not piece:
            break
        raw.extend(piece)
        if len(raw) > max_bytes:
            raise HTTPException(413, f"Upload exceeds {max_bytes} bytes")

    zip_path = dest_dir / "_part.zip"
    zip_path.write_bytes(bytes(raw))
    try:
        safe_extract_csv_zip(
            zip_path,
            dest_dir,
            max_members=int(cfg.worker_zip_max_members),
            max_uncompressed_bytes=int(cfg.worker_zip_max_uncompressed_bytes),
        )
        zip_path.unlink(missing_ok=True)
    except UnsafeArchiveError as e:
        zip_path.unlink(missing_ok=True)
        raise HTTPException(400, f"Unsafe or invalid archive: {e}") from e
    except Exception as e:
        zip_path.unlink(missing_ok=True)
        raise HTTPException(400, f"Failed to extract upload: {e}") from e
    return {"ok": True}


@router.post("/worker-api/ack")
async def worker_ack(
    body: dict,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    w = await _auth_worker(db, authorization)
    _require_active_worker(w)
    job_id = body.get("job_id")
    # IMPORTANT: chunk_id 0 is valid — never use ``x or -1`` (that mapped chunk 0 → 404).
    chunk_id = _body_int(body, "chunk_id", required=True)
    rows = _body_int(body, "rows", default=0)
    job = (await db.execute(select(Job).where(Job.public_id == job_id))).scalar_one_or_none()
    if not job:
        return {"ok": False}

    # Terminal job: still clear this worker's lease so active_leases cannot stick.
    if job.status in ("stopped", "completed", "failed"):
        cleared = await _clear_worker_leases_for_job(
            db, w, job, chunk_id=chunk_id, rows=rows
        )
        if not cleared:
            # Chunk id mismatch / already cleared — still drop any leftover leases.
            await _clear_worker_leases_for_job(db, w, job, rows=rows)
        await db.commit()
        return {"ok": True, "cancelled": True}

    chunk = (
        await db.execute(
            select(JobChunk).where(JobChunk.job_id == job.id, JobChunk.chunk_id == chunk_id)
        )
    ).scalar_one_or_none()
    if not chunk:
        # Never 404 after a successful scrape/upload — free any leases this worker holds.
        # Recompute progress so marking chunks done cannot leave done_searches at 0.
        await _clear_worker_leases_for_job(
            db, w, job, rows=rows, sync_progress=job.status in ("running", "queued")
        )
        pending = (
            await db.execute(
                select(JobChunk).where(JobChunk.job_id == job.id, JobChunk.state != "done")
            )
        ).scalars().first()
        if not pending and job.status == "running":
            zip_path = await jobs_svc.finalize_job(db, job, cancelled=False)
            owner = await db.get(User, job.owner_id)
            if owner:
                await notify_user_telegram(
                    db,
                    owner,
                    f"✅ Job {job.public_id} complete. Businesses: {job.rows_saved}.",
                    Path(zip_path) if zip_path else None,
                )
        else:
            await db.commit()
        return {"ok": True, "cleared": True}

    if chunk.worker_id not in (None, w.id):
        raise HTTPException(403, "Chunk leased to another worker")
    if chunk.state not in ("leased", "done", "pending"):
        raise HTTPException(409, "Chunk is not leased to this worker")

    if chunk.state != "done":
        chunk.state = "done"
        chunk.rows = rows
        chunk.worker_id = w.id
        chunk.leased_at = None
        job.rows_saved += rows
    elif rows and not chunk.rows:
        chunk.rows = rows
        job.rows_saved += rows

    # Always recompute from done chunks (idempotent; heals incremental drift and
    # any path that marked chunks done without bumping done_searches).
    await jobs_svc.sync_job_done_searches(db, job)

    pending = (
        await db.execute(
            select(JobChunk).where(JobChunk.job_id == job.id, JobChunk.state != "done")
        )
    ).scalars().first()
    if not pending and job.status == "running":
        zip_path = await jobs_svc.finalize_job(db, job, cancelled=False)
        owner = await db.get(User, job.owner_id)
        if owner:
            await notify_user_telegram(
                db,
                owner,
                f"✅ Job {job.public_id} complete. Businesses: {job.rows_saved}.",
                Path(zip_path) if zip_path else None,
            )
    else:
        await db.commit()
    return {"ok": True}


# Backward-compatible alias (same handler) if an older agent/docs used /complete.
@router.post("/worker-api/complete")
async def worker_ack_alias(
    body: dict,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    return await worker_ack(body, authorization, db)
