"""Multi-scraper catalog.

All registered sources with ``implemented=True`` are available when enabled
globally (ScraperSettings) and allowed on the user's package. Maps (``gmaps``)
remains the default when ``source`` is omitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SOURCE_GMAPS = "gmaps"

# Menu groups for UI / Telegram
GROUP_MAPS = "maps"
GROUP_COMMERCE = "commerce"
GROUP_SEARCH_EMAIL = "search_email"
GROUP_FACEBOOK = "facebook"
GROUP_SOCIAL = "social"


@dataclass(frozen=True)
class ScraperSpec:
    id: str
    label: str
    group: str
    group_label: str
    description: str
    implemented: bool
    # Rough risk for admin UI (informational)
    risk: str = "medium"
    # Input hints for forms
    inputs: str = "keywords × locations"


SCRAPERS: tuple[ScraperSpec, ...] = (
    ScraperSpec(
        id=SOURCE_GMAPS,
        label="Google Maps",
        group=GROUP_MAPS,
        group_label="Maps & places",
        description="Business leads from Google Maps (keyword × location). Optional website scrape for email/socials.",
        implemented=True,
        risk="managed",
        inputs="keywords × locations",
    ),
    ScraperSpec(
        id="tiktok_shop",
        label="TikTok Shop Creators",
        group=GROUP_COMMERCE,
        group_label="Commerce",
        description="TikTok Shop creator discovery and commerce signals.",
        implemented=True,
        risk="high",
        inputs="niche/keywords × region",
    ),
    ScraperSpec(
        id="google_search",
        label="Google Search",
        group=GROUP_SEARCH_EMAIL,
        group_label="Search & email",
        description="Google SERP (title/URL/snippet). Optional Google dork mode.",
        implemented=True,
        risk="high",
        inputs="keywords × locations (or dork queries)",
    ),
    ScraperSpec(
        id="email_harvest",
        label="Email Harvest",
        group=GROUP_SEARCH_EMAIL,
        group_label="Search & email",
        description="Harvest emails via Google Search channel (SERP → page visit).",
        implemented=True,
        risk="varies",
        inputs="keywords × locations + channels",
    ),
    ScraperSpec(
        id="email_validate",
        label="Email Validator",
        group=GROUP_SEARCH_EMAIL,
        group_label="Search & email",
        description="Validate an uploaded email list (syntax / MX / optional SMTP).",
        implemented=True,
        risk="low",
        inputs="email CSV",
    ),
    ScraperSpec(
        id="facebook_pages",
        label="Facebook Pages",
        group=GROUP_FACEBOOK,
        group_label="Meta / Facebook",
        description="Public Facebook pages (name / email / phone when public).",
        implemented=True,
        risk="very_high",
        inputs="keywords × locations",
    ),
    ScraperSpec(
        id="facebook_groups",
        label="Facebook Groups",
        group=GROUP_FACEBOOK,
        group_label="Meta / Facebook",
        description="Public Facebook groups (name / email / phone when public).",
        implemented=True,
        risk="very_high",
        inputs="keywords × locations",
    ),
    ScraperSpec(
        id="facebook_posts",
        label="Facebook Posts",
        group=GROUP_FACEBOOK,
        group_label="Meta / Facebook",
        description="Facebook posts + contact fields from public pages when available.",
        implemented=True,
        risk="very_high",
        inputs="keywords × locations",
    ),
    ScraperSpec(
        id="facebook_comments",
        label="Facebook Comments",
        group=GROUP_FACEBOOK,
        group_label="Meta / Facebook",
        description="Comment/post URLs; name/email/phone when publicly visible.",
        implemented=True,
        risk="very_high",
        inputs="keywords × locations",
    ),
    ScraperSpec(
        id="instagram",
        label="Instagram",
        group=GROUP_SOCIAL,
        group_label="Social",
        description="Instagram profiles — name, email, phone from public bios when visible.",
        implemented=True,
        risk="very_high",
        inputs="keywords × locations",
    ),
    ScraperSpec(
        id="tiktok",
        label="TikTok",
        group=GROUP_SOCIAL,
        group_label="Social",
        description="TikTok profiles — name, email, phone from public bios when visible.",
        implemented=True,
        risk="very_high",
        inputs="keywords × locations",
    ),
    ScraperSpec(
        id="youtube",
        label="YouTube",
        group=GROUP_SOCIAL,
        group_label="Social",
        description="YouTube channels/videos — name, email, phone when public on channel/about.",
        implemented=True,
        risk="high",
        inputs="keywords × locations",
    ),
    ScraperSpec(
        id="reddit",
        label="Reddit",
        group=GROUP_SOCIAL,
        group_label="Social",
        description="Reddit posts — name, email, phone when public on page/profile.",
        implemented=True,
        risk="high",
        inputs="keywords × locations",
    ),
    ScraperSpec(
        id="pinterest",
        label="Pinterest",
        group=GROUP_SOCIAL,
        group_label="Social",
        description="Pinterest pins/profiles — name, email, phone when public.",
        implemented=True,
        risk="high",
        inputs="keywords × locations",
    ),
    ScraperSpec(
        id="linkedin",
        label="LinkedIn",
        group=GROUP_SOCIAL,
        group_label="Social",
        description="LinkedIn public profiles/companies — name, email, phone when visible (often login-walled).",
        implemented=True,
        risk="extreme",
        inputs="keywords × locations",
    ),
    ScraperSpec(
        id="twitter",
        label="X (Twitter)",
        group=GROUP_SOCIAL,
        group_label="Social",
        description="X/Twitter profiles — name, email, phone from public bios when visible.",
        implemented=True,
        risk="extreme",
        inputs="keywords × locations",
    ),
)

_BY_ID: dict[str, ScraperSpec] = {s.id: s for s in SCRAPERS}

DEFAULT_ENABLED_SOURCES: list[str] = [
    SOURCE_GMAPS,
    "tiktok_shop",
    "google_search",
    "email_harvest",
    "email_validate",
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
]
DEFAULT_ALLOWED_SOURCES: list[str] = list(DEFAULT_ENABLED_SOURCES)

# Channels email_harvest supports on the worker today (more may be added later).
EMAIL_HARVEST_CHANNEL_IDS: frozenset[str] = frozenset({"google_search"})


def all_source_ids() -> list[str]:
    return [s.id for s in SCRAPERS]


def get_scraper(source_id: str | None) -> ScraperSpec | None:
    if not source_id:
        return None
    return _BY_ID.get(str(source_id).strip().lower())


def normalize_source(source_id: str | None) -> str:
    """Return a known source id, defaulting to gmaps."""
    sid = (source_id or "").strip().lower() or SOURCE_GMAPS
    # Aliases
    if sid in ("maps", "google_maps", "google-maps"):
        sid = SOURCE_GMAPS
    if sid in ("x", "twitter_x", "x_twitter"):
        sid = "twitter"
    if sid not in _BY_ID:
        return SOURCE_GMAPS
    return sid


def normalize_source_list(raw: Any, *, default: list[str] | None = None) -> list[str]:
    """Normalize a list of source ids; unknown ids dropped. Empty → default."""
    fallback = list(default if default is not None else DEFAULT_ENABLED_SOURCES)
    if raw is None:
        return fallback
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return fallback
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        sid = normalize_source(str(item) if item is not None else "")
        # normalize_source maps unknown → gmaps; only keep if originally known or gmaps intent
        key = str(item).strip().lower() if item is not None else ""
        if key in ("maps", "google_maps", "google-maps"):
            key = SOURCE_GMAPS
        if key in ("x", "twitter_x", "x_twitter"):
            key = "twitter"
        if key not in _BY_ID:
            continue
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out or fallback


def normalize_channels(raw: Any) -> list[str]:
    """Normalize email_harvest channel list (may be empty)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split(",") if p.strip()]
    if not isinstance(raw, (list, tuple, set)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = str(item).strip().lower() if item is not None else ""
        if key in ("x", "twitter_x", "x_twitter"):
            key = "twitter"
        if key not in EMAIL_HARVEST_CHANNEL_IDS:
            continue
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def spec_to_dict(spec: ScraperSpec, *, selectable: bool) -> dict[str, Any]:
    return {
        "id": spec.id,
        "label": spec.label,
        "group": spec.group,
        "group_label": spec.group_label,
        "description": spec.description,
        "implemented": spec.implemented,
        "risk": spec.risk,
        "inputs": spec.inputs,
        "selectable": selectable,
    }


def catalog_payload(
    *,
    enabled_sources: list[str],
    allowed_sources: list[str] | None,
    is_admin: bool,
) -> list[dict[str, Any]]:
    """Full catalog with selectable flags for the current user context."""
    enabled = set(normalize_source_list(enabled_sources))
    if allowed_sources is None:
        # Admin / no package: treat as all enabled sources
        allowed = set(enabled)
    else:
        allowed = set(normalize_source_list(allowed_sources))
    out: list[dict[str, Any]] = []
    for spec in SCRAPERS:
        site_ok = spec.id in enabled
        pkg_ok = is_admin or spec.id in allowed
        selectable = bool(spec.implemented and site_ok and pkg_ok)
        out.append(spec_to_dict(spec, selectable=selectable))
    return out
