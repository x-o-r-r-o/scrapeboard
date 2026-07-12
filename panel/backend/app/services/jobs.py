"""Job helpers: create from inputs, merge chunk CSVs, zip results, notify."""

from __future__ import annotations

import csv
import io
import secrets
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Job, JobChunk, ScrapeSettings, User
from app.services.billing import active_subscription

DEFAULT_USER_PERMS = {
    "can_run": True,
    "can_stop": True,
    "can_upload_inputs": True,
    "max_threads": 4,
    "allowed_engines": "all",
}


def effective_perms(user: User) -> dict:
    if user.role == "admin":
        return {**DEFAULT_USER_PERMS, "can_run": True, "can_stop": True, "can_upload_inputs": True, "max_threads": 999}
    return {**DEFAULT_USER_PERMS, **(user.perms or {})}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def job_parts_dir(public_id: str) -> Path:
    d = get_settings().results_dir / public_id / "parts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def job_merge_dir(public_id: str) -> Path:
    d = get_settings().results_dir / public_id / "merged"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def create_job_from_bytes(
    db: AsyncSession,
    user: User,
    kw_bytes: bytes,
    loc_bytes: bytes,
    overrides: dict | None = None,
) -> Job:
    perms = effective_perms(user)
    if not perms.get("can_run") and user.role != "admin":
        raise PermissionError("No run permission")
    if not perms.get("can_upload_inputs") and user.role != "admin":
        raise PermissionError("No upload permission")

    sub = await active_subscription(db, user)
    if user.role != "admin" and not sub:
        raise PermissionError("Active subscription required")

    cfg = get_settings()
    max_mb = 5
    if user.role == "admin":
        max_mb = 200
    elif sub:
        max_mb = int(getattr(sub, "max_upload_mb", None) or perms.get("max_upload_mb") or 5)
    max_bytes = max(1, max_mb) * 1024 * 1024
    if len(kw_bytes) + len(loc_bytes) > max_bytes:
        raise ValueError(f"Upload exceeds plan limit ({max_mb} MB)")

    settings_row = await db.get(ScrapeSettings, 1)
    overrides = overrides or {}
    settings = {
        "engine": overrides.get("engine") or (settings_row.engine if settings_row else "chrome"),
        "threads": int(overrides.get("threads") or (settings_row.threads if settings_row else 2)),
        "scrape_websites": overrides.get("scrape_websites")
        or (settings_row.scrape_websites if settings_row else "yes"),
        "max_results": int(
            overrides["max_results"]
            if overrides.get("max_results") is not None
            else (settings_row.max_results if settings_row else 0)
        ),
    }

    thread_cap = int(perms.get("max_threads") or DEFAULT_USER_PERMS["max_threads"])
    if user.role != "admin" and sub:
        thread_cap = min(thread_cap, sub.threads)
    settings["threads"] = min(int(settings["threads"]), thread_cap)

    ae = perms.get("allowed_engines", "all")
    if ae != "all" and settings["engine"] not in ae and user.role != "admin":
        raise PermissionError(f"Engine {settings['engine']} not allowed")

    cfg = get_settings()
    job_dir = cfg.uploads_dir / f"user_{user.id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    public_id = f"{user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"
    kw_path = job_dir / f"{public_id}_keywords.txt"
    loc_path = job_dir / f"{public_id}_locations.txt"
    kw_path.write_bytes(kw_bytes)
    loc_path.write_bytes(loc_bytes)

    kw_lines = [
        ln.strip()
        for ln in kw_bytes.decode("utf-8", errors="ignore").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    loc_lines = [
        ln.strip()
        for ln in loc_bytes.decode("utf-8", errors="ignore").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    if not kw_lines or not loc_lines:
        raise ValueError("Keywords and locations must be non-empty")

    total = len(kw_lines) * len(loc_lines)
    chunk_size = settings_row.chunk_size if settings_row else 500
    job = Job(
        public_id=public_id,
        owner_id=user.id,
        status="queued",
        settings=settings,
        keywords_path=str(kw_path),
        locations_path=str(loc_path),
        total_searches=total,
    )
    db.add(job)
    await db.flush()

    cid = 0
    for start in range(0, total, chunk_size):
        db.add(
            JobChunk(
                job_id=job.id,
                chunk_id=cid,
                start_index=start,
                end_index=min(start + chunk_size, total),
                state="pending",
            )
        )
        cid += 1

    await db.commit()
    await db.refresh(job)
    return job


def merge_job_csvs(public_id: str) -> Path | None:
    """Merge all part CSVs into per-location files and return zip path."""
    parts = job_parts_dir(public_id)
    merge = job_merge_dir(public_id)
    by_loc: dict[str, list[dict]] = {}
    seen: dict[str, set[tuple[str, str]]] = {}

    for csv_path in parts.rglob("*.csv"):
        try:
            with open(csv_path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    loc = row.get("query_location") or "results"
                    key = (row.get("name") or "", row.get("address") or "")
                    bucket = seen.setdefault(loc, set())
                    if key in bucket:
                        continue
                    bucket.add(key)
                    by_loc.setdefault(loc, []).append(row)
        except OSError:
            continue

    if not by_loc:
        return None

    fieldnames = [
        "keyword",
        "query_location",
        "name",
        "address",
        "phone",
        "email",
        "website",
        "facebook",
        "instagram",
        "twitter",
        "linkedin",
        "youtube",
        "tiktok",
        "pinterest",
        "whatsapp",
        "telegram",
        "maps_url",
    ]
    written: list[Path] = []
    for loc, rows in by_loc.items():
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in loc)[:80]
        out = merge / f"{safe}.csv"
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        written.append(out)

    zip_path = get_settings().results_dir / public_id / f"results_{public_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in written:
            zf.write(p, p.name)
    return zip_path


async def finalize_job(db: AsyncSession, job: Job, cancelled: bool = False) -> Path | None:
    zip_path = merge_job_csvs(job.public_id)
    if zip_path:
        job.result_zip = str(zip_path)
    if cancelled and job.status not in ("completed", "stopped", "failed"):
        job.status = "stopped"
    elif not cancelled and job.status == "running":
        job.status = "completed"
    job.finished_at = utcnow()
    await db.commit()
    await db.refresh(job)
    return zip_path


async def own_active_job(db: AsyncSession, user: User) -> Job | None:
    q = (
        select(Job)
        .where(Job.owner_id == user.id, Job.status.in_(("queued", "running")))
        .order_by(Job.id.desc())
    )
    return (await db.execute(q)).scalars().first()
