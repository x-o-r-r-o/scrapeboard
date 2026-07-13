"""Scraper catalog + global enable flags (Phase A multi-source)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_ready_user
from app.core.database import get_db
from app.models import Package, User
from app.schemas import (
    ScraperCatalogItem,
    ScraperSettingsOut,
    ScraperSettingsUpdate,
)
from app.services.billing import active_subscription
from app.services.scraper_registry import (
    SCRAPERS,
    catalog_payload,
    normalize_source_list,
)
from app.services.scraper_settings import get_scraper_settings, set_enabled_sources

router = APIRouter(tags=["scrapers"])


async def _allowed_for_user(db: AsyncSession, user: User) -> list[str] | None:
    """Package allowed_sources for non-admins; None means admin (all enabled)."""
    if user.role == "admin":
        return None
    sub = await active_subscription(db, user)
    if not sub or not getattr(sub, "package_id", None):
        return ["gmaps"]
    pkg = await db.get(Package, sub.package_id)
    if not pkg:
        return ["gmaps"]
    return normalize_source_list(getattr(pkg, "allowed_sources", None))


@router.get("/scrapers", response_model=list[ScraperCatalogItem])
async def list_scrapers(
    user: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    """Sources available to the current user (selectable = can create a job)."""
    site = await get_scraper_settings(db)
    allowed = await _allowed_for_user(db, user)
    rows = catalog_payload(
        enabled_sources=list(site.enabled_sources or []),
        allowed_sources=allowed,
        is_admin=user.role == "admin",
    )
    return [ScraperCatalogItem(**r) for r in rows]


@router.get("/settings/scrapers", response_model=ScraperSettingsOut)
async def get_scraper_flags(
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    site = await get_scraper_settings(db)
    enabled = normalize_source_list(site.enabled_sources)
    catalog = catalog_payload(
        enabled_sources=enabled,
        allowed_sources=None,
        is_admin=True,
    )
    return ScraperSettingsOut(
        enabled_sources=enabled,
        catalog=[ScraperCatalogItem(**r) for r in catalog],
    )


@router.put("/settings/scrapers", response_model=ScraperSettingsOut)
async def update_scraper_flags(
    body: ScraperSettingsUpdate,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    site = await set_enabled_sources(db, body.enabled_sources)
    enabled = normalize_source_list(site.enabled_sources)
    catalog = catalog_payload(
        enabled_sources=enabled,
        allowed_sources=None,
        is_admin=True,
    )
    return ScraperSettingsOut(
        enabled_sources=enabled,
        catalog=[ScraperCatalogItem(**r) for r in catalog],
    )


@router.get("/scrapers/registry", response_model=list[ScraperCatalogItem])
async def full_registry(
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
):
    """Admin: full static registry (ignores enable flags)."""
    return [
        ScraperCatalogItem(
            id=s.id,
            label=s.label,
            group=s.group,
            group_label=s.group_label,
            description=s.description,
            implemented=s.implemented,
            risk=s.risk,
            inputs=s.inputs,
            selectable=s.implemented,
        )
        for s in SCRAPERS
    ]
