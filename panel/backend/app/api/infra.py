from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from sqlalchemy import select, update
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
from app.models import Job, JobChunk, ProxyPool, ScrapeSettings, User, WorkerNode
from app.schemas import (
    ProxyPoolCreate,
    ProxyPoolOut,
    ProxyPoolUpdate,
    ScrapeSettingsOut,
    ScrapeSettingsUpdate,
    WorkerCreate,
    WorkerCreateResponse,
    WorkerOut,
    WorkerUpdate,
)
from app.services import jobs as jobs_svc
from app.services.notify import notify_user_telegram
from app.services.safe_zip import UnsafeArchiveError, safe_extract_csv_zip
from app.services.worker_config import (
    apply_worker_config_update,
    merge_lease_settings,
    normalize_worker_config,
    public_worker_config,
    scrape_settings_to_config,
)

router = APIRouter(tags=["infra"])


def _proxy_count(text: str) -> int:
    return sum(1 for line in (text or "").splitlines() if line.strip() and not line.strip().startswith("#"))


def _scrape_out(s: ScrapeSettings) -> ScrapeSettingsOut:
    return ScrapeSettingsOut(
        engine=s.engine,
        threads=s.threads,
        block_resources=s.block_resources,
        scrape_websites=s.scrape_websites,
        max_results=s.max_results,
        chunk_size=s.chunk_size,
        min_delay=s.min_delay,
        max_delay=s.max_delay,
        cooldown_every=s.cooldown_every,
        cooldown_min=s.cooldown_min,
        cooldown_max=s.cooldown_max,
        captcha_provider=s.captcha_provider,
        captcha_key_configured=bool(s.captcha_key),
        captcha_host=s.captcha_host,
        captcha_retries=s.captcha_retries,
        nav_timeout=s.nav_timeout,
        proxy_attempts=s.proxy_attempts,
        headless=bool(getattr(s, "headless", True)),
        no_stealth=bool(getattr(s, "no_stealth", False)),
        browser_path=str(getattr(s, "browser_path", "") or ""),
        geoip=bool(getattr(s, "geoip", False)),
        preflight_timeout=float(getattr(s, "preflight_timeout", 12.0) or 12.0),
        no_preflight=bool(getattr(s, "no_preflight", False)),
        fresh=bool(getattr(s, "fresh", False)),
        debug=bool(getattr(s, "debug", False)),
    )


def _worker_out(w: WorkerNode, scrape: ScrapeSettings | None = None) -> WorkerOut:
    online = False
    if w.last_seen_at:
        ts = w.last_seen_at if w.last_seen_at.tzinfo else w.last_seen_at.replace(tzinfo=timezone.utc)
        online = datetime.now(timezone.utc) - ts < timedelta(seconds=90)
    raw = w.worker_config or {}
    if not raw and scrape is not None:
        raw = scrape_settings_to_config(scrape)
    return WorkerOut(
        id=w.id,
        name=w.name,
        token_prefix=w.token_prefix,
        is_enabled=w.is_enabled,
        is_draining=w.is_draining,
        max_browsers=w.max_browsers,
        proxy_pool_id=w.proxy_pool_id,
        last_seen_at=w.last_seen_at,
        cpu_percent=w.cpu_percent,
        mem_percent=w.mem_percent,
        version=w.version,
        online=online,
        worker_config=public_worker_config(raw),
    )


def _redact_worker_config(cfg: dict) -> dict:
    """Heartbeat-safe config: never send captcha_key over the wire on heartbeat."""
    out = dict(cfg or {})
    if out.get("captcha_key"):
        out["captcha_key"] = ""
        out["captcha_key_configured"] = True
    else:
        out["captcha_key_configured"] = False
    out.pop("captcha_key", None)
    return out


def _set_worker_token(w: WorkerNode, raw: str) -> None:
    w.token_hash = hash_password(raw)
    w.token_prefix = raw[:8]
    w.token_lookup = hash_worker_token(raw)


def _install_hint(token: str) -> str:
    return (
        "python agent.py --setup\n"
        "# or:\n"
        f"python agent.py --panel-url https://scrape.cvmso.com --token {token}"
    )


# --- proxy pools ---

@router.get("/proxy-pools", response_model=list[ProxyPoolOut])
async def list_pools(_: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ProxyPool).order_by(ProxyPool.id))).scalars().all()
    return [
        ProxyPoolOut(id=p.id, name=p.name, description=p.description, proxy_count=_proxy_count(p.proxies_text), is_active=p.is_active)
        for p in rows
    ]


@router.post("/proxy-pools", response_model=ProxyPoolOut)
async def create_pool(body: ProxyPoolCreate, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    p = ProxyPool(**body.model_dump())
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return ProxyPoolOut(id=p.id, name=p.name, description=p.description, proxy_count=_proxy_count(p.proxies_text), is_active=p.is_active)


@router.get("/proxy-pools/{pool_id}")
async def get_pool(pool_id: int, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    p = await db.get(ProxyPool, pool_id)
    if not p:
        raise HTTPException(404, "Not found")
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "proxies_text": p.proxies_text,
        "proxy_count": _proxy_count(p.proxies_text),
        "is_active": p.is_active,
    }


@router.patch("/proxy-pools/{pool_id}", response_model=ProxyPoolOut)
async def update_pool(pool_id: int, body: ProxyPoolUpdate, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    p = await db.get(ProxyPool, pool_id)
    if not p:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    await db.commit()
    await db.refresh(p)
    return ProxyPoolOut(id=p.id, name=p.name, description=p.description, proxy_count=_proxy_count(p.proxies_text), is_active=p.is_active)


@router.delete("/proxy-pools/{pool_id}")
async def delete_pool(pool_id: int, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    p = await db.get(ProxyPool, pool_id)
    if not p:
        raise HTTPException(404, "Not found")
    await db.delete(p)
    await db.commit()
    return {"detail": "Deleted"}


# --- workers ---

@router.get("/workers", response_model=list[WorkerOut])
async def list_workers(_: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    scrape = await db.get(ScrapeSettings, 1)
    rows = (await db.execute(select(WorkerNode).order_by(WorkerNode.id))).scalars().all()
    return [_worker_out(w, scrape) for w in rows]


@router.post("/workers", response_model=WorkerCreateResponse)
async def create_worker(body: WorkerCreate, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    raw = generate_worker_token()
    scrape = await db.get(ScrapeSettings, 1)
    if body.use_global_scrape_defaults or body.worker_config is None:
        cfg = scrape_settings_to_config(scrape)
    else:
        cfg = normalize_worker_config({})
    if body.worker_config is not None:
        cfg = apply_worker_config_update(cfg, body.worker_config.model_dump(exclude_unset=True))
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
    scrape = await db.get(ScrapeSettings, 1)
    return WorkerCreateResponse(worker=_worker_out(w, scrape), token=raw, install_hint=_install_hint(raw))


@router.patch("/workers/{worker_id}", response_model=WorkerOut)
async def update_worker(worker_id: int, body: WorkerUpdate, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    w = await db.get(WorkerNode, worker_id)
    if not w:
        raise HTTPException(404, "Not found")
    data = body.model_dump(exclude_unset=True)
    reset = data.pop("reset_config_from_global", False)
    cfg_patch = data.pop("worker_config", None)
    for k, v in data.items():
        setattr(w, k, v)
    if reset:
        scrape = await db.get(ScrapeSettings, 1)
        w.worker_config = scrape_settings_to_config(scrape)
    elif cfg_patch is not None:
        w.worker_config = apply_worker_config_update(w.worker_config or {}, cfg_patch)
    await db.commit()
    await db.refresh(w)
    scrape = await db.get(ScrapeSettings, 1)
    return _worker_out(w, scrape)


@router.post("/workers/{worker_id}/rotate-token", response_model=WorkerCreateResponse)
async def rotate_token(worker_id: int, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    w = await db.get(WorkerNode, worker_id)
    if not w:
        raise HTTPException(404, "Not found")
    raw = generate_worker_token()
    _set_worker_token(w, raw)
    await db.commit()
    await db.refresh(w)
    scrape = await db.get(ScrapeSettings, 1)
    return WorkerCreateResponse(
        worker=_worker_out(w, scrape),
        token=raw,
        install_hint=_install_hint(raw),
    )


# --- scrape settings ---

@router.get("/settings/scrape", response_model=ScrapeSettingsOut)
async def get_scrape(_: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    s = await db.get(ScrapeSettings, 1) or ScrapeSettings(id=1)
    if not await db.get(ScrapeSettings, 1):
        db.add(s)
        await db.commit()
        await db.refresh(s)
    return _scrape_out(s)


@router.put("/settings/scrape", response_model=ScrapeSettingsOut)
async def update_scrape(body: ScrapeSettingsUpdate, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    s = await db.get(ScrapeSettings, 1)
    if not s:
        s = ScrapeSettings(id=1)
        db.add(s)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    await db.commit()
    await db.refresh(s)
    return _scrape_out(s)


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
    w.version = str(body.get("version") or w.version)
    await db.commit()
    scrape = await db.get(ScrapeSettings, 1)
    effective = merge_lease_settings(
        scrape=scrape,
        worker_config=w.worker_config or {},
        job_settings={},
        max_browsers=w.max_browsers,
    )
    return {
        "ok": True,
        "drain": w.is_draining,
        "enabled": w.is_enabled,
        "name": w.name,
        "max_browsers": w.max_browsers,
        "proxy_pool_id": w.proxy_pool_id,
        # Redacted: captcha_key only delivered inside job leases
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

    job = (
        await db.execute(select(Job).where(Job.status == "running").order_by(Job.id))
    ).scalars().first()
    if not job:
        queued = (
            await db.execute(select(Job).where(Job.status == "queued").order_by(Job.id))
        ).scalars().first()
        if queued:
            queued.status = "running"
            queued.started_at = datetime.now(timezone.utc)
            await db.commit()
            job = queued
        else:
            return {"chunk": None}

    # reclaim stale leases
    stale_before = datetime.now(timezone.utc) - timedelta(seconds=120)
    stale = (
        await db.execute(
            select(JobChunk).where(JobChunk.job_id == job.id, JobChunk.state == "leased")
        )
    ).scalars().all()
    for c in stale:
        leased = c.leased_at
        if leased and (leased if leased.tzinfo else leased.replace(tzinfo=timezone.utc)) < stale_before:
            c.state = "pending"
            c.worker_id = None
    await db.commit()

    await db.refresh(job)
    if job.status == "stopped":
        return {"chunk": None}

    # Atomic claim: only one worker wins the pending → leased transition
    claimed: JobChunk | None = None
    for _ in range(5):
        candidate = (
            await db.execute(
                select(JobChunk)
                .where(JobChunk.job_id == job.id, JobChunk.state == "pending")
                .order_by(JobChunk.chunk_id)
            )
        ).scalars().first()
        if not candidate:
            return {"chunk": None}
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

    scrape = await db.get(ScrapeSettings, 1)
    proxies_text = ""
    if w.proxy_pool_id:
        pool = await db.get(ProxyPool, w.proxy_pool_id)
        if pool and pool.is_active:
            proxies_text = pool.proxies_text

    settings = merge_lease_settings(
        scrape=scrape,
        worker_config=w.worker_config or {},
        job_settings=dict(job.settings or {}),
        max_browsers=w.max_browsers,
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
            "keywords": keywords,
            "locations": locations,
            "settings": settings,
            "proxies_text": proxies_text,
            "ts": job.public_id,
        },
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

    dest_dir = jobs_svc.job_parts_dir(job.public_id) / str(chunk_id)
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
