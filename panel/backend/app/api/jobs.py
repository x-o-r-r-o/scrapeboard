from datetime import datetime, timezone
from pathlib import Path
import shutil

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_ready_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models import Job, User
from app.schemas import JobFileEntry, JobFilesOut, JobOut, MessageOut, StorageOwnerOut
from app.services import jobs as jobs_svc
from app.services.notify import notify_user_telegram
from app.services.perms import effective_perms

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _result_meta(j: Job) -> tuple[bool, int | None]:
    if j.result_zip and Path(j.result_zip).exists():
        try:
            return True, Path(j.result_zip).stat().st_size
        except OSError:
            return True, None
    root = jobs_svc.job_result_root(j.public_id, j.owner_id)
    zip_path = root / f"results_{j.public_id}.zip"
    if zip_path.exists():
        try:
            return True, zip_path.stat().st_size
        except OSError:
            return True, None
    return False, None


async def _job_out(db: AsyncSession, j: Job) -> JobOut:
    pct = 100.0 * j.done_searches / j.total_searches if j.total_searches else 0.0
    owner = await db.get(User, j.owner_id)
    exists, size = _result_meta(j)
    return JobOut(
        id=j.id,
        public_id=j.public_id,
        owner_id=j.owner_id,
        owner_username=owner.username if owner else None,
        owner_telegram_id=owner.telegram_id if owner else None,
        status=j.status,
        settings=j.settings or {},
        total_searches=j.total_searches,
        done_searches=j.done_searches,
        rows_saved=j.rows_saved,
        result_zip=None if not exists else (j.result_zip or f"results_{j.public_id}.zip"),
        result_exists=exists,
        result_bytes=size,
        error=j.error,
        created_at=j.created_at,
        started_at=j.started_at,
        finished_at=j.finished_at,
        pct=pct,
    )


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


@router.get("", response_model=list[JobOut])
async def list_jobs(
    user: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
    owner_id: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    query = select(Job).order_by(Job.id.desc()).limit(limit)
    if user.role != "admin":
        query = query.where(Job.owner_id == user.id)
    elif owner_id is not None:
        query = query.where(Job.owner_id == owner_id)
    if status_filter:
        query = query.where(Job.status == status_filter)
    if q:
        query = query.where(Job.public_id.contains(q))
    rows = (await db.execute(query)).scalars().all()
    return [await _job_out(db, j) for j in rows]


@router.get("/admin/storage", response_model=list[StorageOwnerOut])
async def storage_by_owner(
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    cfg = get_settings()
    users = (await db.execute(select(User).order_by(User.id))).scalars().all()
    out: list[StorageOwnerOut] = []
    for u in users:
        job_count = int(
            (await db.execute(select(func.count()).select_from(Job).where(Job.owner_id == u.id))).scalar_one() or 0
        )
        uploads = _dir_size(cfg.uploads_dir / f"user_{u.id}") + _dir_size(cfg.uploads_dir / f"tg_{u.id}")
        # Prefer per-user result tree; also sum any legacy flat job dirs
        results = _dir_size(cfg.results_dir / f"user_{u.id}")
        jobs = (await db.execute(select(Job.public_id).where(Job.owner_id == u.id))).scalars().all()
        for pid in jobs:
            legacy = cfg.results_dir / pid
            scoped = cfg.results_dir / f"user_{u.id}" / pid
            if legacy.exists() and not scoped.exists():
                results += _dir_size(legacy)
        if job_count == 0 and uploads == 0 and results == 0:
            continue
        out.append(
            StorageOwnerOut(
                user_id=u.id,
                username=u.username,
                telegram_id=u.telegram_id,
                uploads_bytes=uploads,
                results_bytes=results,
                job_count=job_count,
            )
        )
    return out


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: int, user: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    j = await db.get(Job, job_id)
    if not j:
        raise HTTPException(404, "Not found")
    if user.role != "admin" and j.owner_id != user.id:
        raise HTTPException(403, "Forbidden")
    return await _job_out(db, j)


@router.post("", response_model=JobOut)
async def create_job(
    engine: str | None = Form(default=None),
    threads: int | None = Form(default=None),
    scrape_websites: str | None = Form(default=None),
    max_results: int | None = Form(default=None),
    keywords: UploadFile = File(...),
    locations: UploadFile = File(...),
    user: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    overrides = {}
    if engine:
        overrides["engine"] = engine
    if threads is not None:
        overrides["threads"] = threads
    if scrape_websites:
        overrides["scrape_websites"] = scrape_websites
    if max_results is not None:
        overrides["max_results"] = max_results
    try:
        job = await jobs_svc.create_job_from_bytes(
            db,
            user,
            await keywords.read(),
            await locations.read(),
            overrides,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return await _job_out(db, job)


@router.post("/{job_id}/stop", response_model=JobOut)
async def stop_job(job_id: int, user: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    perms = effective_perms(user)
    if not perms.get("can_stop") and user.role != "admin":
        raise HTTPException(403, "No stop permission")
    j = await db.get(Job, job_id)
    if not j:
        raise HTTPException(404, "Not found")
    if user.role != "admin" and j.owner_id != user.id:
        raise HTTPException(403, "Forbidden")
    if j.status in ("queued", "running"):
        zip_path = await jobs_svc.finalize_job(db, j, cancelled=True)
        owner = await db.get(User, j.owner_id)
        if owner:
            msg = f"⏹ Job {j.public_id} stopped. Rows so far: {j.rows_saved}."
            await notify_user_telegram(db, owner, msg, Path(zip_path) if zip_path else None)
        await db.refresh(j)
    return await _job_out(db, j)


@router.get("/{job_id}/download")
async def download_job(job_id: int, user: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    j = await db.get(Job, job_id)
    if not j:
        raise HTTPException(404, "Not found")
    if user.role != "admin" and j.owner_id != user.id:
        raise HTTPException(403, "Forbidden")
    perms = effective_perms(user)
    if user.role != "admin" and not perms.get("can_download", True):
        raise HTTPException(403, "No download permission")
    if not j.result_zip or not Path(j.result_zip).exists():
        zip_path = jobs_svc.merge_job_csvs(j.public_id, j.owner_id)
        if zip_path:
            j.result_zip = str(zip_path)
            await db.commit()
        else:
            raise HTTPException(404, "No results yet")
    return FileResponse(j.result_zip, filename=Path(j.result_zip).name, media_type="application/zip")


@router.get("/{job_id}/files", response_model=JobFilesOut)
async def list_job_files(job_id: int, user: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    j = await db.get(Job, job_id)
    if not j:
        raise HTTPException(404, "Not found")
    if user.role != "admin" and j.owner_id != user.id:
        raise HTTPException(403, "Forbidden")
    cfg = get_settings()
    files: list[JobFileEntry] = []
    result_root = jobs_svc.job_result_root(j.public_id, j.owner_id)
    roots = [
        (result_root, "result"),
        (cfg.uploads_dir / f"user_{j.owner_id}", "input"),
        (cfg.uploads_dir / f"tg_{j.owner_id}", "input"),
    ]
    # Also scan legacy flat result dir if distinct
    legacy = cfg.results_dir / j.public_id
    if legacy.exists() and legacy.resolve() != result_root.resolve():
        roots.insert(0, (legacy, "result"))
    for root, kind_base in roots:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            # for uploads only include this job's files
            if kind_base == "input" and j.public_id not in p.name:
                continue
            name = str(p.relative_to(root)) if p.is_relative_to(root) else p.name
            kind = kind_base
            if p.suffix == ".zip":
                kind = "zip"
            elif "parts" in p.parts:
                kind = "part"
            elif "merged" in p.parts:
                kind = "merged"
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            files.append(JobFileEntry(name=name, path=str(p), size_bytes=size, kind=kind))
    # Don't expose absolute paths to non-admins
    if user.role != "admin":
        for f in files:
            f.path = f.name
    return JobFilesOut(
        job_id=j.id,
        public_id=j.public_id,
        files=files,
        total_bytes=sum(f.size_bytes for f in files),
    )


@router.delete("/{job_id}/files", response_model=MessageOut)
async def purge_job_files(
    job_id: int,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    j = await db.get(Job, job_id)
    if not j:
        raise HTTPException(404, "Not found")
    cfg = get_settings()
    for result_dir in (
        jobs_svc.job_result_root(j.public_id, j.owner_id),
        cfg.results_dir / j.public_id,
    ):
        if result_dir.exists():
            shutil.rmtree(result_dir, ignore_errors=True)
    # clear named uploads for this job
    upload_dir = cfg.uploads_dir / f"user_{j.owner_id}"
    if upload_dir.exists():
        for p in upload_dir.glob(f"{j.public_id}*"):
            try:
                p.unlink()
            except OSError:
                pass
    j.result_zip = None
    await db.commit()
    return MessageOut(detail="Job files purged")


@router.delete("/{job_id}", response_model=MessageOut)
async def delete_job(
    job_id: int,
    purge_files: bool = True,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    j = await db.get(Job, job_id)
    if not j:
        raise HTTPException(404, "Not found")
    if j.status in ("queued", "running"):
        raise HTTPException(400, "Stop the job before deleting")
    public_id = j.public_id
    owner_id = j.owner_id
    await db.delete(j)
    await db.commit()
    if purge_files:
        cfg = get_settings()
        for result_dir in (
            jobs_svc.job_result_root(public_id, owner_id),
            cfg.results_dir / public_id,
        ):
            if result_dir.exists():
                shutil.rmtree(result_dir, ignore_errors=True)
        upload_dir = cfg.uploads_dir / f"user_{owner_id}"
        if upload_dir.exists():
            for p in upload_dir.glob(f"{public_id}*"):
                try:
                    p.unlink()
                except OSError:
                    pass
    return MessageOut(detail="Job deleted")
