"""Scrape profile helpers: default profile, clone for packages, resolve for workers."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ScrapeSettings, WorkerNode
from app.services.worker_config import copy_profile_fields, scrape_settings_to_config


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "profile")[:60]


async def ensure_default_profile(db: AsyncSession) -> ScrapeSettings:
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
        description="Global default scrape profile for workers and new packages",
        is_default=True,
        is_active=True,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def get_default_profile(db: AsyncSession) -> ScrapeSettings:
    row = (
        await db.execute(
            select(ScrapeSettings)
            .where(ScrapeSettings.is_default == True, ScrapeSettings.is_active == True)  # noqa: E712
            .order_by(ScrapeSettings.id)
        )
    ).scalars().first()
    if row:
        return row
    return await ensure_default_profile(db)


async def resolve_scrape_for_worker(db: AsyncSession, worker: WorkerNode) -> ScrapeSettings:
    if worker.scrape_settings_id:
        profile = await db.get(ScrapeSettings, worker.scrape_settings_id)
        if profile and profile.is_active:
            return profile
    return await get_default_profile(db)


async def clone_profile(
    db: AsyncSession,
    *,
    name: str,
    slug: str | None = None,
    description: str = "",
    source: ScrapeSettings | None = None,
    is_default: bool = False,
) -> ScrapeSettings:
    src = source or await get_default_profile(db)
    base_slug = _slugify(slug or name)
    slug_final = base_slug
    n = 2
    while (
        await db.execute(select(ScrapeSettings).where(ScrapeSettings.slug == slug_final))
    ).scalar_one_or_none():
        slug_final = f"{base_slug}-{n}"
        n += 1
    dest = ScrapeSettings(
        name=name[:128],
        slug=slug_final,
        description=description or f"Cloned from {src.name}",
        is_default=is_default,
        is_active=True,
    )
    copy_profile_fields(src, dest)
    db.add(dest)
    await db.commit()
    await db.refresh(dest)
    return dest


async def ensure_workers_have_default_profile(db: AsyncSession) -> int:
    """Assign default scrape profile to any worker missing one. Returns count updated."""
    default = await get_default_profile(db)
    workers = (
        await db.execute(
            select(WorkerNode).where(
                (WorkerNode.scrape_settings_id == None)  # noqa: E711
                | (WorkerNode.scrape_settings_id == 0)
            )
        )
    ).scalars().all()
    n = 0
    for w in workers:
        w.scrape_settings_id = default.id
        if not w.worker_config:
            w.worker_config = scrape_settings_to_config(default)
        n += 1
    if n:
        await db.commit()
    return n
