from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

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
    WorkerOut,
    WorkerUpdate,
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

async def _enrich_worker(db: AsyncSession, w: WorkerNode, leases: dict[int, int] | None = None) -> WorkerOut:
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


@router.post("/worker-api/heartbeat")
async def worker_heartbeat(
    body: dict,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    w = await _auth_worker(db, authorization)
    w.last_seen_at = datetime.now(timezone.utc)
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
    await db.commit()
    captcha = await get_captcha_settings(db)
    # Heartbeat sync uses worker machine config only (no job/package context yet)
    effective = merge_lease_settings(
        package_defaults=None,
        worker_config=w.worker_config or {},
        job_settings={},
        max_browsers=w.max_browsers,
        captcha=captcha,
    )
    return {
        "ok": True,
        "drain": w.is_draining,
        "enabled": w.is_enabled,
        "name": w.name,
        "max_browsers": w.max_browsers,
        "proxy_pool_id": w.proxy_pool_id,
        # Redacted: captcha keys only delivered inside job leases
        "worker_config": _redact_worker_config(effective),
    }


@router.post("/worker-api/lease")
async def worker_lease(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    w = await _auth_worker(db, authorization)
    if w.is_draining or not w.is_enabled:
        return {"chunk": None}

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
        return {"chunk": None, "slots_full": True, "active_leases": active_leases, "max_browsers": slot_cap}

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
        return {"chunk": None}

    # reclaim stale leases (global for this worker + current job)
    stale_before = datetime.now(timezone.utc) - timedelta(seconds=120)
    stale = (
        await db.execute(
            select(JobChunk).where(
                JobChunk.state == "leased",
                or_(JobChunk.job_id == job.id, JobChunk.worker_id == w.id),
            )
        )
    ).scalars().all()
    for c in stale:
        leased = c.leased_at
        if leased and (leased if leased.tzinfo else leased.replace(tzinfo=timezone.utc)) < stale_before:
            c.state = "pending"
            c.worker_id = None
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
        return {"chunk": None, "slots_full": True, "active_leases": active_leases, "max_browsers": slot_cap}

    await db.refresh(job)
    if job.status == "stopped":
        return {"chunk": None}
    # Queued jobs must still fit thread quota at claim time
    if job.status == "queued" and not await jobs_svc.can_start_job(db, job):
        return {"chunk": None}

    # Atomic claim: only one worker wins the pending → leased transition
    claimed: JobChunk | None = None
    tried: set[int] = set()
    for _ in range(8):
        if job.status == "queued" and not await jobs_svc.can_start_job(db, job):
            tried.add(job.id)
            job = await _next_job(skip_ids=tried)
            if not job:
                return {"chunk": None}
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
                return {"chunk": None}
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
    if not claimed:
        return {"chunk": None}

    # Promote queued → running only after a successful claim (thread quota consumed now)
    await db.refresh(job)
    if job.status == "queued":
        if not await jobs_svc.can_start_job(db, job):
            # Lost race on quota — release the chunk and leave job queued
            claimed.state = "pending"
            claimed.worker_id = None
            claimed.leased_at = None
            await db.commit()
            return {"chunk": None}
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
                return {"chunk": None}

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

    return {
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
    if job.status not in ("running", "queued"):
        raise HTTPException(409, "Job is not active")
    await _require_chunk_lease(db, w, job, chunk_id)

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
    chunk_id = int(body.get("chunk_id") or -1)
    rows = int(body.get("rows") or 0)
    job = (await db.execute(select(Job).where(Job.public_id == job_id))).scalar_one_or_none()
    if not job:
        return {"ok": False}
    if job.status == "stopped":
        return {"ok": True, "cancelled": True}

    chunk = await _require_chunk_lease(db, w, job, chunk_id)
    if chunk.state != "done":
        chunk.state = "done"
        chunk.rows = rows
        chunk.worker_id = w.id
        job.done_searches = min(
            job.total_searches,
            job.done_searches + (chunk.end_index - chunk.start_index),
        )
        job.rows_saved += rows

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
