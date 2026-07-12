from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_ready_user
from app.core.database import get_db
from app.models import Job, User
from app.schemas import JobOut
from app.services import jobs as jobs_svc
from app.services.notify import notify_user_telegram

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _job_out(j: Job) -> JobOut:
    pct = 100.0 * j.done_searches / j.total_searches if j.total_searches else 0.0
    return JobOut(
        id=j.id,
        public_id=j.public_id,
        owner_id=j.owner_id,
        status=j.status,
        settings=j.settings or {},
        total_searches=j.total_searches,
        done_searches=j.done_searches,
        rows_saved=j.rows_saved,
        result_zip=j.result_zip,
        error=j.error,
        created_at=j.created_at,
        started_at=j.started_at,
        finished_at=j.finished_at,
        pct=pct,
    )


@router.get("", response_model=list[JobOut])
async def list_jobs(user: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    q = select(Job).order_by(Job.id.desc())
    if user.role != "admin":
        q = q.where(Job.owner_id == user.id)
    rows = (await db.execute(q)).scalars().all()
    return [_job_out(j) for j in rows]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: int, user: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    j = await db.get(Job, job_id)
    if not j:
        raise HTTPException(404, "Not found")
    if user.role != "admin" and j.owner_id != user.id:
        raise HTTPException(403, "Forbidden")
    return _job_out(j)


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
    return _job_out(job)


@router.post("/{job_id}/stop", response_model=JobOut)
async def stop_job(job_id: int, user: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    perms = jobs_svc.effective_perms(user)
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
    return _job_out(j)


@router.get("/{job_id}/download")
async def download_job(job_id: int, user: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    j = await db.get(Job, job_id)
    if not j:
        raise HTTPException(404, "Not found")
    if user.role != "admin" and j.owner_id != user.id:
        raise HTTPException(403, "Forbidden")
    if not j.result_zip or not Path(j.result_zip).exists():
        # try merge now
        zip_path = jobs_svc.merge_job_csvs(j.public_id)
        if zip_path:
            j.result_zip = str(zip_path)
            await db.commit()
        else:
            raise HTTPException(404, "No results yet")
    return FileResponse(j.result_zip, filename=Path(j.result_zip).name, media_type="application/zip")
