"""Legacy scrape-profile helpers + one-time migration into package/worker JSON.

Scrape profiles are no longer a first-class admin surface. Package.scrape_defaults
and WorkerNode.worker_config own scrape flags. The scrape_settings table remains
for captcha one-time migrate and as a seed source for existing DBs.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Package, ScrapeSettings, WorkerNode
from app.services.worker_config import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_WORKER_CONFIG,
    build_package_scrape_defaults,
    normalize_worker_config,
    scrape_settings_to_config,
)


async def ensure_default_profile(db: AsyncSession) -> ScrapeSettings:
    """Ensure id=1 default row exists (legacy / captcha migrate seed only)."""
    row = await db.get(ScrapeSettings, 1)
    if row:
        if not getattr(row, "name", None):
            row.name = "Default"
        if not getattr(row, "slug", None):
            row.slug = "default"
        row.is_default = True
        row.is_active = True
        await db.commit()
        await db.refresh(row)
        return row
    row = ScrapeSettings(
        id=1,
        name="Default",
        slug="default",
        description="Legacy default (migrated into package scrape_defaults)",
        is_default=True,
        is_active=True,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def migrate_packages_from_profiles(db: AsyncSession) -> int:
    """Copy linked scrape profile (or defaults) into Package.scrape_defaults when empty."""
    await ensure_default_profile(db)
    packages = (await db.execute(select(Package).order_by(Package.id))).scalars().all()
    n = 0
    for pkg in packages:
        existing = pkg.scrape_defaults if isinstance(getattr(pkg, "scrape_defaults", None), dict) else {}
        needs = not existing
        if needs:
            scrape_row = None
            if getattr(pkg, "scrape_settings_id", None):
                scrape_row = await db.get(ScrapeSettings, pkg.scrape_settings_id)
            pkg.scrape_defaults = build_package_scrape_defaults(
                threads=pkg.threads,
                scrape_row=scrape_row,
            )
            if scrape_row is not None and getattr(scrape_row, "chunk_size", None):
                try:
                    pkg.chunk_size = max(1, int(scrape_row.chunk_size))
                except (TypeError, ValueError):
                    pkg.chunk_size = DEFAULT_CHUNK_SIZE
            elif not getattr(pkg, "chunk_size", None):
                pkg.chunk_size = DEFAULT_CHUNK_SIZE
            n += 1
        else:
            # Keep threads aligned with package allowance
            cfg = dict(existing)
            cfg["threads"] = pkg.threads
            pkg.scrape_defaults = normalize_worker_config(cfg)
            if not getattr(pkg, "chunk_size", None):
                pkg.chunk_size = DEFAULT_CHUNK_SIZE
    if n:
        await db.commit()
    else:
        await db.commit()
    return n


async def ensure_workers_have_config(db: AsyncSession) -> int:
    """Seed empty worker_config from legacy profile or built-in defaults. Returns count updated."""
    await ensure_default_profile(db)
    workers = (await db.execute(select(WorkerNode).order_by(WorkerNode.id))).scalars().all()
    n = 0
    for w in workers:
        if w.worker_config:
            # Strip legacy captcha keys by normalizing
            w.worker_config = normalize_worker_config(w.worker_config)
            continue
        scrape_row = None
        if getattr(w, "scrape_settings_id", None):
            scrape_row = await db.get(ScrapeSettings, w.scrape_settings_id)
        if scrape_row:
            w.worker_config = scrape_settings_to_config(scrape_row)
        else:
            w.worker_config = dict(DEFAULT_WORKER_CONFIG)
        n += 1
    await db.commit()
    return n


# Back-compat alias used by older call sites
async def ensure_workers_have_default_profile(db: AsyncSession) -> int:
    await migrate_packages_from_profiles(db)
    return await ensure_workers_have_config(db)
