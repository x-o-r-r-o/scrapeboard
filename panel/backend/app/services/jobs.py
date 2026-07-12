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
from app.models import Job, JobChunk, Package, User
from app.services.billing import active_subscription
from app.services.worker_config import DEFAULT_CHUNK_SIZE, package_defaults_from_package

from app.services.perms import DEFAULT_USER_PERMS, effective_perms


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def job_thread_count(job: Job) -> int:
    """Threads requested/consumed by a job (from settings JSON)."""
    try:
        return max(1, int((job.settings or {}).get("threads") or 1))
    except (TypeError, ValueError):
        return 1


async def user_thread_allowance(db: AsyncSession, user: User) -> int:
    """Max concurrent browser threads this user may run across all jobs."""
    perms = effective_perms(user)
    cap = int(perms.get("max_threads") or DEFAULT_USER_PERMS["max_threads"])
    if user.role == "admin":
        return max(1, cap)
    sub = await active_subscription(db, user)
    if sub:
        cap = min(cap, int(sub.threads or cap))
    return max(1, cap)


async def sum_running_threads(
    db: AsyncSession,
    owner_id: int,
    *,
    exclude_job_id: int | None = None,
) -> int:
    """Sum of settings.threads for this owner's currently running jobs."""
    rows = (
        await db.execute(select(Job).where(Job.owner_id == owner_id, Job.status == "running"))
    ).scalars().all()
    total = 0
    for j in rows:
        if exclude_job_id is not None and j.id == exclude_job_id:
            continue
        total += job_thread_count(j)
    return total


async def free_thread_slots(db: AsyncSession, user: User) -> int:
    allowance = await user_thread_allowance(db, user)
    used = await sum_running_threads(db, user.id)
    return max(0, allowance - used)


async def can_start_job(db: AsyncSession, job: Job) -> bool:
    """True if promoting this queued job would stay within the owner's thread quota."""
    owner = await db.get(User, job.owner_id)
    if not owner:
        return False
    need = job_thread_count(job)
    used = await sum_running_threads(db, job.owner_id, exclude_job_id=job.id if job.status == "running" else None)
    allowance = await user_thread_allowance(db, owner)
    return used + need <= allowance


async def thread_quota_snapshot(db: AsyncSession, user: User) -> dict:
    allowance = await user_thread_allowance(db, user)
    used = await sum_running_threads(db, user.id)
    return {
        "thread_allowance": allowance,
        "threads_in_use": used,
        "threads_free": max(0, allowance - used),
    }


def owner_id_from_public_id(public_id: str) -> int | None:
    """public_id is `{user_id}_{timestamp}_{hex}`."""
    try:
        return int(str(public_id).split("_", 1)[0])
    except (TypeError, ValueError, IndexError):
        return None


def job_result_root(public_id: str, owner_id: int | None = None) -> Path:
    """Per-user isolated result tree: results/user_{id}/{public_id}/.

    Falls back to legacy results/{public_id}/ when that exists (read path).
    New writes always use the user-scoped layout when owner_id is known.
    """
    cfg = get_settings()
    oid = owner_id if owner_id is not None else owner_id_from_public_id(public_id)
    if oid is not None:
        scoped = cfg.results_dir / f"user_{oid}" / public_id
        legacy = cfg.results_dir / public_id
        # Prefer existing tree (legacy or scoped); default new work to scoped
        if scoped.exists() or not legacy.exists():
            return scoped
        return legacy
    return cfg.results_dir / public_id


def job_parts_dir(public_id: str, owner_id: int | None = None) -> Path:
    d = job_result_root(public_id, owner_id) / "parts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def job_merge_dir(public_id: str, owner_id: int | None = None) -> Path:
    d = job_result_root(public_id, owner_id) / "merged"
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

    pkg = None
    package_engines = None
    if user.role != "admin" and sub and getattr(sub, "package_id", None):
        pkg = await db.get(Package, sub.package_id)
        if pkg:
            package_engines = pkg.allowed_engines
    defaults = package_defaults_from_package(pkg)
    overrides = overrides or {}
    settings = {
        "engine": overrides.get("engine") or defaults.get("engine") or "chrome",
        "threads": int(overrides.get("threads") or defaults.get("threads") or 2),
        "scrape_websites": overrides.get("scrape_websites")
        or defaults.get("scrape_websites")
        or "yes",
        "max_results": int(
            overrides["max_results"]
            if overrides.get("max_results") is not None
            else (defaults.get("max_results") if defaults.get("max_results") is not None else 0)
        ),
    }

    thread_cap = await user_thread_allowance(db, user)
    requested = max(1, int(settings["threads"]))
    if requested > thread_cap:
        raise ValueError(
            f"Threads ({requested}) exceed your allowance ({thread_cap}). "
            f"Lower threads or wait for running jobs to free capacity."
        )
    settings["threads"] = requested
    # Jobs always start queued; lease promotes only when free threads cover this job.
    free = await free_thread_slots(db, user)
    settings["queued_for_threads"] = requested > free

    ae = package_engines if package_engines else perms.get("allowed_engines", "all")
    if ae and ae != "all" and settings["engine"] not in ae and user.role != "admin":
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
    try:
        chunk_size = max(1, int(getattr(pkg, "chunk_size", None) or DEFAULT_CHUNK_SIZE))
    except (TypeError, ValueError):
        chunk_size = DEFAULT_CHUNK_SIZE
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


async def update_queued_job_settings(
    db: AsyncSession,
    job: Job,
    *,
    threads: int | None = None,
    engine: str | None = None,
) -> Job:
    """Edit settings on a queued job (e.g. lower threads to fit free quota)."""
    if job.status != "queued":
        raise ValueError("Only queued jobs can be edited")
    owner = await db.get(User, job.owner_id)
    if not owner:
        raise ValueError("Owner not found")
    settings = dict(job.settings or {})
    if threads is not None:
        threads = max(1, int(threads))
        cap = await user_thread_allowance(db, owner)
        if threads > cap:
            raise ValueError(f"Threads ({threads}) exceed allowance ({cap})")
        settings["threads"] = threads
    if engine is not None:
        settings["engine"] = str(engine).strip() or settings.get("engine") or "chrome"
    need = max(1, int(settings.get("threads") or 1))
    free = await free_thread_slots(db, owner)
    settings["queued_for_threads"] = need > free
    job.settings = settings
    await db.commit()
    await db.refresh(job)
    return job


def merge_job_csvs(public_id: str, owner_id: int | None = None) -> Path | None:
    """Merge all part CSVs into per-location files and return zip path."""
    oid = owner_id if owner_id is not None else owner_id_from_public_id(public_id)
    parts = job_parts_dir(public_id, oid)
    merge = job_merge_dir(public_id, oid)
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

    root = job_result_root(public_id, oid)
    root.mkdir(parents=True, exist_ok=True)
    zip_path = root / f"results_{public_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in written:
            zf.write(p, p.name)
    return zip_path


async def finalize_job(db: AsyncSession, job: Job, cancelled: bool = False) -> Path | None:
    zip_path = merge_job_csvs(job.public_id, job.owner_id)
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
