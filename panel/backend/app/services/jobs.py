"""Job helpers: create from inputs, merge chunk CSVs, zip results, notify."""

from __future__ import annotations

import csv
import io
import math
import secrets
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Job, JobChunk, Package, User, UserWorker, WorkerNode
from app.services.billing import active_subscription, get_billing, user_has_dedicated_worker
from app.services.input_files import InputFileError, entries_to_bytes, validate_pair
from app.services.worker_config import DEFAULT_CHUNK_SIZE, package_defaults_from_package

from app.services.perms import DEFAULT_USER_PERMS, effective_perms


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


JOB_NAME_MAX_LEN = 128


def normalize_job_name(raw: str | None, *, max_len: int = JOB_NAME_MAX_LEN) -> str | None:
    """Optional display name: strip, collapse whitespace; empty → None."""
    if raw is None:
        return None
    s = " ".join(str(raw).split()).strip()
    if not s:
        return None
    return s[:max_len]


def job_display_label(job: Job) -> str:
    """Human label for Telegram/logs: name (public_id) when set, else public_id."""
    n = (getattr(job, "name", None) or "").strip()
    if n:
        return f"{n} ({job.public_id})"
    return job.public_id


async def recomputed_done_searches(db: AsyncSession, job: Job) -> int:
    """Sum searches from chunks in state=done (source of truth for completed progress)."""
    raw = (
        await db.execute(
            select(func.coalesce(func.sum(JobChunk.end_index - JobChunk.start_index), 0)).where(
                JobChunk.job_id == job.id,
                JobChunk.state == "done",
            )
        )
    ).scalar_one()
    return min(int(job.total_searches or 0), max(0, int(raw or 0)))


async def leased_live_progress(db: AsyncSession, job: Job) -> tuple[int, int]:
    """Sum best-effort in-flight progress from leased chunks (searches, rows)."""
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(JobChunk.progress_done), 0),
                func.coalesce(func.sum(JobChunk.progress_rows), 0),
            ).where(JobChunk.job_id == job.id, JobChunk.state == "leased")
        )
    ).one()
    return max(0, int(row[0] or 0)), max(0, int(row[1] or 0))


async def live_job_progress(db: AsyncSession, job: Job) -> tuple[int, int]:
    """Display progress for running jobs: done chunks + leased in-flight.

    Returns ``(done_searches, rows_saved)``. Completed/stopped jobs should use
    persisted ``job.done_searches`` / ``job.rows_saved`` instead.
    """
    base_done = await recomputed_done_searches(db, job)
    live_done, live_rows = await leased_live_progress(db, job)
    total = int(job.total_searches or 0)
    done = min(total, base_done + live_done) if total else base_done + live_done
    rows = max(0, int(job.rows_saved or 0) + live_rows)
    return done, rows


async def sync_job_done_searches(db: AsyncSession, job: Job) -> int:
    """Persist done_searches from done chunk ranges (idempotent; ignores live)."""
    n = await recomputed_done_searches(db, job)
    job.done_searches = n
    return n


def clear_chunk_live_progress(chunk: JobChunk) -> None:
    """Reset in-flight counters (ack, reclaim to pending, cancel)."""
    chunk.progress_done = 0
    chunk.progress_rows = 0


def apply_chunk_live_progress(
    chunk: JobChunk,
    *,
    done_in_chunk: int | None = None,
    rows: int | None = None,
) -> None:
    """Update leased-chunk live counters (monotonic within the lease)."""
    if chunk.state != "leased":
        return
    size = max(0, int(chunk.end_index) - int(chunk.start_index))
    if done_in_chunk is not None:
        n = max(0, int(done_in_chunk))
        if size:
            n = min(n, size)
        # Monotonic: workers may report out-of-order / stale heartbeats.
        chunk.progress_done = max(int(chunk.progress_done or 0), n)
    if rows is not None:
        chunk.progress_rows = max(int(chunk.progress_rows or 0), max(0, int(rows)))


def effective_chunk_size(total: int, package_chunk_size: int, parallel_slots: int) -> int:
    """Searches per chunk: package ``chunk_size`` is a ceiling (max), never exceeded.

    Default package max is ``DEFAULT_CHUNK_SIZE`` (500). We may shrink below that
    so work can run in parallel across ``parallel_slots`` (eligible workers, or
    for a dedicated pin the machine's ``max_browsers`` capacity):

    - 200 searches, 4 slots, max 500 → chunk 50 (4×50), not one 200 chunk
    - 2000 searches, 4 slots, max 500 → chunk 500 (4×500; more chunks if larger)
    - 100 searches, 1 slot, max 500 → chunk 100 (no fake fleet split)

    Never creates more chunks than searches (chunk size ≥ 1). Lease already
    allows concurrent pending→leased claims of different chunks of the same job.
    """
    if total <= 0:
        return 1
    pkg = max(1, int(package_chunk_size or DEFAULT_CHUNK_SIZE))
    slots = max(1, int(parallel_slots or 1))
    by_parallel = max(1, math.ceil(total / slots))
    return min(pkg, by_parallel)


async def parallel_worker_count_for_user(db: AsyncSession, user: User) -> int:
    """Parallel chunk slots for this user when creating a job.

    Shared / unpinned dedicated: one slot per enabled non-draining worker so a
    job can spread across the fleet (not the sum of every machine's
    ``max_browsers``).

    Dedicated with pins: only pinned machines count, and each contributes
    ``max_browsers`` slots (concurrent leases on that box). A single pin with
    ``max_browsers=4`` can get up to 4 chunks; we never invent slots from
    unpinned fleet workers.
    """
    workers = (
        await db.execute(
            select(WorkerNode).where(
                WorkerNode.is_enabled == True,  # noqa: E712
                WorkerNode.is_draining == False,  # noqa: E712
            )
        )
    ).scalars().all()
    if not workers:
        return 1
    pinned_mode = False
    if await user_has_dedicated_worker(db, user):
        pinned = (
            await db.execute(select(UserWorker.worker_id).where(UserWorker.user_id == user.id))
        ).scalars().all()
        if pinned:
            allowed = {int(x) for x in pinned}
            workers = [w for w in workers if w.id in allowed]
            pinned_mode = True
            if not workers:
                return 1
    if pinned_mode:
        return max(1, sum(max(1, int(w.max_browsers or 1)) for w in workers))
    return max(1, len(workers))


def job_thread_count(job: Job) -> int:
    """Threads requested/consumed by a job (from settings JSON)."""
    try:
        return max(1, int((job.settings or {}).get("threads") or 1))
    except (TypeError, ValueError):
        return 1


async def user_thread_allowance(db: AsyncSession, user: User) -> int:
    """Max browser threads for this user's single running job (plan/perm cap)."""
    perms = effective_perms(user)
    cap = int(perms.get("max_threads") or DEFAULT_USER_PERMS["max_threads"])
    if user.role == "admin":
        return max(1, cap)
    sub = await active_subscription(db, user)
    if sub:
        cap = min(cap, int(sub.threads or cap))
    return max(1, cap)


async def owner_blocking_job(
    db: AsyncSession,
    owner_id: int,
    *,
    exclude_job_id: int | None = None,
) -> Job | None:
    """Return another job that blocks starting a new one for this owner.

    Policy: at most one active scrape job per owner_id. A job blocks others when
    its status is ``running``, or when it still holds leased chunks (in-flight
    work after a race / before finalize clears leases).
    """
    q = select(Job).where(Job.owner_id == owner_id, Job.status == "running")
    if exclude_job_id is not None:
        q = q.where(Job.id != exclude_job_id)
    q = q.order_by(Job.id)
    running = (await db.execute(q)).scalars().first()
    if running:
        return running

    # Sticky / in-flight leases on another active job still occupy the owner slot.
    lease_q = (
        select(Job)
        .join(JobChunk, JobChunk.job_id == Job.id)
        .where(
            Job.owner_id == owner_id,
            Job.status.in_(("queued", "running")),
            JobChunk.state == "leased",
        )
        .order_by(Job.id)
    )
    if exclude_job_id is not None:
        lease_q = lease_q.where(Job.id != exclude_job_id)
    return (await db.execute(lease_q)).scalars().first()


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
    """Threads available for a *new* job. Zero while another job holds the owner slot."""
    if await owner_blocking_job(db, user.id):
        return 0
    return await user_thread_allowance(db, user)


async def can_start_job(db: AsyncSession, job: Job) -> bool:
    """True if this job may be promoted/leased: no other active job for the owner,
    and its thread count fits the owner's allowance."""
    owner = await db.get(User, job.owner_id)
    if not owner:
        return False
    if await owner_blocking_job(db, job.owner_id, exclude_job_id=job.id):
        return False
    need = job_thread_count(job)
    allowance = await user_thread_allowance(db, owner)
    return need <= allowance


async def thread_quota_snapshot(db: AsyncSession, user: User) -> dict:
    allowance = await user_thread_allowance(db, user)
    used = await sum_running_threads(db, user.id)
    # One job at a time: free is 0 while any job holds the owner slot.
    free = 0 if await owner_blocking_job(db, user.id) else max(0, allowance - used)
    return {
        "thread_allowance": allowance,
        "threads_in_use": used,
        "threads_free": free,
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


def _unlink_quiet(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def purge_job_storage(
    public_id: str,
    owner_id: int,
    *,
    keywords_path: str | None = None,
    locations_path: str | None = None,
    result_zip: str | None = None,
) -> None:
    """Remove result trees and input uploads for a job from disk."""
    cfg = get_settings()
    result_dirs = {
        job_result_root(public_id, owner_id),
        cfg.results_dir / public_id,
        cfg.results_dir / f"user_{owner_id}" / public_id,
    }
    for result_dir in result_dirs:
        if result_dir.exists():
            shutil.rmtree(result_dir, ignore_errors=True)

    for upload_dir in (
        cfg.uploads_dir / f"user_{owner_id}",
        cfg.uploads_dir / f"tg_{owner_id}",
    ):
        if not upload_dir.exists():
            continue
        for p in upload_dir.glob(f"{public_id}*"):
            _unlink_quiet(p)

    for raw in (keywords_path, locations_path, result_zip):
        if raw:
            _unlink_quiet(Path(raw))


async def purge_and_delete_job(db: AsyncSession, job: Job, *, purge_files: bool = True) -> None:
    """Hard-delete job row + chunks, optionally wipe on-disk storage."""
    public_id = job.public_id
    owner_id = job.owner_id
    keywords_path = job.keywords_path
    locations_path = job.locations_path
    result_zip = job.result_zip
    # Chunks have a non-nullable FK without DB CASCADE; delete them first or
    # SQLAlchemy tries to NULL job_id and the flush fails.
    await db.execute(delete(JobChunk).where(JobChunk.job_id == job.id))
    await db.delete(job)
    await db.commit()
    if purge_files:
        purge_job_storage(
            public_id,
            owner_id,
            keywords_path=keywords_path,
            locations_path=locations_path,
            result_zip=result_zip,
        )


async def create_job_from_bytes(
    db: AsyncSession,
    user: User,
    kw_bytes: bytes,
    loc_bytes: bytes,
    overrides: dict | None = None,
    *,
    name: str | None = None,
    keywords_name: str | None = None,
    locations_name: str | None = None,
    check_ext: bool = False,
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

    billing = await get_billing(db)
    try:
        kw_lines, loc_lines = validate_pair(
            kw_bytes,
            loc_bytes,
            keywords_name=keywords_name,
            locations_name=locations_name,
            configured_extensions=billing.allowed_extensions,
            check_ext=check_ext,
        )
    except InputFileError as e:
        raise ValueError(str(e)) from e

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
            f"Lower threads or wait for your current job to finish."
        )
    settings["threads"] = requested
    # Jobs always start queued; lease promotes only when this owner has no other
    # active job and threads fit the allowance (one job at a time per owner).
    free = await free_thread_slots(db, user)
    settings["queued_for_threads"] = requested > free

    ae = package_engines if package_engines else perms.get("allowed_engines", "all")
    if ae and ae != "all" and settings["engine"] not in ae and user.role != "admin":
        raise PermissionError(f"Engine {settings['engine']} not allowed")

    job_dir = cfg.uploads_dir / f"user_{user.id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    public_id = f"{user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"
    kw_path = job_dir / f"{public_id}_keywords.txt"
    loc_path = job_dir / f"{public_id}_locations.txt"
    # Persist normalized UTF-8 line files only after validation succeeds.
    kw_path.write_bytes(entries_to_bytes(kw_lines))
    loc_path.write_bytes(entries_to_bytes(loc_lines))

    total = len(kw_lines) * len(loc_lines)
    try:
        pkg_chunk = max(1, int(getattr(pkg, "chunk_size", None) or DEFAULT_CHUNK_SIZE))
    except (TypeError, ValueError):
        pkg_chunk = DEFAULT_CHUNK_SIZE
    parallel_workers = await parallel_worker_count_for_user(db, user)
    chunk_size = effective_chunk_size(total, pkg_chunk, parallel_workers)
    settings["chunk_size"] = chunk_size
    settings["chunk_parallel_workers"] = parallel_workers
    # Prefer explicit name=; Telegram may also pass name/title in overrides.
    display_name = normalize_job_name(name)
    if display_name is None and overrides:
        display_name = normalize_job_name(
            overrides.get("name") if overrides.get("name") is not None else overrides.get("title")
        )
    job = Job(
        public_id=public_id,
        owner_id=user.id,
        name=display_name,
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
    name: str | None = None,
    set_name: bool = False,
) -> Job:
    """Edit settings on a queued job (threads/engine) and/or optional display name.

    Name may be changed while queued or running. Threads/engine remain queued-only.
    Pass set_name=True to apply name (including clearing with empty string).
    """
    if set_name:
        if job.status not in ("queued", "running"):
            raise ValueError("Name can only be edited on queued or running jobs")
        job.name = normalize_job_name(name)

    if threads is not None or engine is not None:
        if job.status != "queued":
            raise ValueError("Only queued jobs can edit threads/engine")
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

    if not set_name and threads is None and engine is None:
        raise ValueError("No changes")
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


async def clear_open_chunks(db: AsyncSession, job: Job) -> int:
    """Mark remaining pending/leased chunks done so active_leases drop immediately.

    Does not bump done_searches/rows — only frees worker capacity after stop/complete.
    Queues cancel hints on workers that still held leases so agents stop mid-scrape
    even after the lease rows are cleared.
    """
    from app.models import WorkerNode  # local import avoids cycles at module load

    open_chunks = (
        await db.execute(
            select(JobChunk).where(JobChunk.job_id == job.id, JobChunk.state != "done")
        )
    ).scalars().all()
    notify_workers: dict[int, None] = {}
    for c in open_chunks:
        if c.worker_id and c.state == "leased":
            notify_workers[c.worker_id] = None
        c.state = "done"
        c.worker_id = None
        c.leased_at = None

    if notify_workers and job.public_id:
        now_iso = utcnow().isoformat()
        for wid in notify_workers:
            w = await db.get(WorkerNode, wid)
            if not w:
                continue
            meta = dict(w.meta or {})
            cancels = dict(meta.get("cancel_jobs") or {})
            # public_id → first seen ISO; heartbeat returns keys younger than TTL
            cancels[job.public_id] = cancels.get(job.public_id) or now_iso
            # Bound map size
            if len(cancels) > 40:
                oldest = sorted(cancels.items(), key=lambda kv: kv[1])[: len(cancels) - 40]
                for k, _ in oldest:
                    cancels.pop(k, None)
            meta["cancel_jobs"] = cancels
            w.meta = meta
    return len(open_chunks)


async def finalize_job(db: AsyncSession, job: Job, cancelled: bool = False) -> Path | None:
    zip_path = merge_job_csvs(job.public_id, job.owner_id)
    if zip_path:
        job.result_zip = str(zip_path)
    if cancelled and job.status not in ("completed", "stopped", "failed"):
        job.status = "stopped"
    elif not cancelled and job.status == "running":
        job.status = "completed"
    job.finished_at = utcnow()
    # Always clear leftover leases so dashboard active_leases cannot stick at 1
    # after stop/complete (in-flight scrapes are cancelled via heartbeat cancel_jobs).
    await clear_open_chunks(db, job)
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
