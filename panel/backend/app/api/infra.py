import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_ready_user
from app.core.database import get_db
from app.core.security import hash_password, verify_password
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

router = APIRouter(tags=["infra"])


def _proxy_count(text: str) -> int:
    return sum(1 for line in (text or "").splitlines() if line.strip() and not line.strip().startswith("#"))


def _worker_out(w: WorkerNode) -> WorkerOut:
    online = False
    if w.last_seen_at:
        ts = w.last_seen_at if w.last_seen_at.tzinfo else w.last_seen_at.replace(tzinfo=timezone.utc)
        online = datetime.now(timezone.utc) - ts < timedelta(seconds=90)
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
    rows = (await db.execute(select(WorkerNode).order_by(WorkerNode.id))).scalars().all()
    return [_worker_out(w) for w in rows]


@router.post("/workers", response_model=WorkerCreateResponse)
async def create_worker(body: WorkerCreate, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    raw = secrets.token_urlsafe(32)
    w = WorkerNode(
        name=body.name,
        token_hash=hash_password(raw),
        token_prefix=raw[:8],
        max_browsers=body.max_browsers,
        proxy_pool_id=body.proxy_pool_id,
    )
    db.add(w)
    await db.commit()
    await db.refresh(w)
    hint = (
        f'python agent.py --setup\n'
        f'# or:\n'
        f'python agent.py --panel-url https://scrape.cvmso.com --token {raw}'
    )
    return WorkerCreateResponse(worker=_worker_out(w), token=raw, install_hint=hint)


@router.patch("/workers/{worker_id}", response_model=WorkerOut)
async def update_worker(worker_id: int, body: WorkerUpdate, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    w = await db.get(WorkerNode, worker_id)
    if not w:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(w, k, v)
    await db.commit()
    await db.refresh(w)
    return _worker_out(w)


@router.post("/workers/{worker_id}/rotate-token", response_model=WorkerCreateResponse)
async def rotate_token(worker_id: int, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    w = await db.get(WorkerNode, worker_id)
    if not w:
        raise HTTPException(404, "Not found")
    raw = secrets.token_urlsafe(32)
    w.token_hash = hash_password(raw)
    w.token_prefix = raw[:8]
    await db.commit()
    await db.refresh(w)
    return WorkerCreateResponse(
        worker=_worker_out(w),
        token=raw,
        install_hint=(
            f"python agent.py --setup\n"
            f"# or:\n"
            f"python agent.py --panel-url https://scrape.cvmso.com --token {raw}"
        ),
    )


# --- scrape settings ---

@router.get("/settings/scrape", response_model=ScrapeSettingsOut)
async def get_scrape(_: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    s = await db.get(ScrapeSettings, 1) or ScrapeSettings(id=1)
    if not await db.get(ScrapeSettings, 1):
        db.add(s)
        await db.commit()
        await db.refresh(s)
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
    )


@router.put("/settings/scrape", response_model=ScrapeSettingsOut)
async def update_scrape(body: ScrapeSettingsUpdate, _: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    s = await db.get(ScrapeSettings, 1)
    if not s:
        s = ScrapeSettings(id=1)
        db.add(s)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    await db.commit()
    return await get_scrape(_, __, db)


# --- worker agent protocol ---

async def _auth_worker(db: AsyncSession, authorization: str | None) -> WorkerNode:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Worker token required")
    token = authorization.removeprefix("Bearer ").strip()
    workers = (await db.execute(select(WorkerNode).where(WorkerNode.is_enabled == True))).scalars().all()  # noqa: E712
    for w in workers:
        if verify_password(token, w.token_hash):
            return w
    raise HTTPException(403, "Invalid worker token")


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
    return {"ok": True, "drain": w.is_draining, "enabled": w.is_enabled}


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
        # activate next queued
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

    chunk = (
        await db.execute(
            select(JobChunk)
            .where(JobChunk.job_id == job.id, JobChunk.state == "pending")
            .order_by(JobChunk.chunk_id)
        )
    ).scalars().first()
    # reclaim stale leases
    if not chunk:
        stale_before = datetime.now(timezone.utc) - timedelta(seconds=120)
        stale = (
            await db.execute(
                select(JobChunk).where(JobChunk.job_id == job.id, JobChunk.state == "leased")
            )
        ).scalars().all()
        for c in stale:
            if c.leased_at and (c.leased_at if c.leased_at.tzinfo else c.leased_at.replace(tzinfo=timezone.utc)) < stale_before:
                c.state = "pending"
                c.worker_id = None
        await db.commit()
        chunk = (
            await db.execute(
                select(JobChunk)
                .where(JobChunk.job_id == job.id, JobChunk.state == "pending")
                .order_by(JobChunk.chunk_id)
            )
        ).scalars().first()

    if not chunk:
        return {"chunk": None}

    # don't lease for stopped jobs
    await db.refresh(job)
    if job.status == "stopped":
        return {"chunk": None}

    chunk.state = "leased"
    chunk.worker_id = w.id
    chunk.leased_at = datetime.now(timezone.utc)
    await db.commit()

    scrape = await db.get(ScrapeSettings, 1)
    proxies_text = ""
    if w.proxy_pool_id:
        pool = await db.get(ProxyPool, w.proxy_pool_id)
        if pool:
            proxies_text = pool.proxies_text

    settings = dict(job.settings or {})
    if scrape:
        for k in (
            "engine", "threads", "block_resources", "scrape_websites", "max_results",
            "min_delay", "max_delay", "cooldown_every", "cooldown_min", "cooldown_max",
            "captcha_provider", "captcha_key", "captcha_host", "captcha_retries",
            "nav_timeout", "proxy_attempts",
        ):
            settings.setdefault(k, getattr(scrape, k))

    # load keyword/location lines for this job
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
            "id": chunk.chunk_id,
            "start": chunk.start_index,
            "end": chunk.end_index,
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
    await _auth_worker(db, authorization)
    job = (await db.execute(select(Job).where(Job.public_id == job_id))).scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")
    dest_dir = jobs_svc.job_parts_dir(job.public_id) / str(chunk_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    raw = await file.read()
    zip_path = dest_dir / "_part.zip"
    zip_path.write_bytes(raw)
    # extract csvs
    import zipfile

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(dest_dir)
        zip_path.unlink(missing_ok=True)
    except Exception:
        pass
    return {"ok": True}


@router.post("/worker-api/ack")
async def worker_ack(
    body: dict,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    w = await _auth_worker(db, authorization)
    job_id = body.get("job_id")
    chunk_id = int(body.get("chunk_id") or -1)
    rows = int(body.get("rows") or 0)
    job = (await db.execute(select(Job).where(Job.public_id == job_id))).scalar_one_or_none()
    if not job:
        return {"ok": False}
    if job.status == "stopped":
        return {"ok": True, "cancelled": True}

    chunk = (
        await db.execute(
            select(JobChunk).where(JobChunk.job_id == job.id, JobChunk.chunk_id == chunk_id)
        )
    ).scalar_one_or_none()
    if chunk and chunk.state != "done":
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
