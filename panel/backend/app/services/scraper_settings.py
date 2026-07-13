"""Global scraper enable/disable singleton (Phase A multi-source)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ScraperSettings
from app.services.scraper_registry import (
    DEFAULT_ENABLED_SOURCES,
    normalize_source_list,
)


async def ensure_scraper_settings(db: AsyncSession) -> ScraperSettings:
    row = await db.get(ScraperSettings, 1)
    if row is None:
        row = ScraperSettings(id=1, enabled_sources=list(DEFAULT_ENABLED_SOURCES))
        db.add(row)
        await db.flush()
        return row
    # Normalize; upgrade Phase A defaults / add newly shipped sources when already multi-scraper
    current = list(row.enabled_sources or [])
    normalized = normalize_source_list(current)
    if current == ["gmaps"] or current == [] or not current:
        normalized = list(DEFAULT_ENABLED_SOURCES)
    else:
        extras: list[str] = []
        if "email_validate" not in normalized and (
            "email_harvest" in normalized or "google_search" in normalized
        ):
            extras.append("email_validate")
        # Phase E — add if site already enabled any non-Maps scraper
        if any(
            s in normalized
            for s in (
                "tiktok_shop",
                "google_search",
                "email_harvest",
                "email_validate",
                "youtube",
            )
        ):
            for sid in (
                "youtube",
                "reddit",
                "pinterest",
                "tiktok",
                "facebook_pages",
                "facebook_groups",
                "facebook_posts",
                "facebook_comments",
                "instagram",
                "linkedin",
                "twitter",
            ):
                if sid not in normalized and sid not in extras:
                    extras.append(sid)
        if extras:
            normalized = list(normalized) + extras
    if list(row.enabled_sources or []) != normalized:
        row.enabled_sources = normalized
        await db.flush()
    return row


async def get_scraper_settings(db: AsyncSession) -> ScraperSettings:
    return await ensure_scraper_settings(db)


async def set_enabled_sources(db: AsyncSession, sources: list) -> ScraperSettings:
    row = await ensure_scraper_settings(db)
    # Always keep gmaps enabled — Maps must never be turned off via this switch
    normalized = normalize_source_list(sources)
    if "gmaps" not in normalized:
        normalized = ["gmaps", *normalized]
    row.enabled_sources = normalized
    await db.commit()
    await db.refresh(row)
    return row
