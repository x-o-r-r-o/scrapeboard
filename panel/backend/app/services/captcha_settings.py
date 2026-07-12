"""Global captcha settings (singleton) + one-time migrate from scrape profiles."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CaptchaSettings, ScrapeSettings, WorkerNode

CAPTCHA_KEYS: tuple[str, ...] = (
    "captcha_provider",
    "captcha_key",
    "captcha_host",
    "captcha_retries",
    "captcha_backup_provider",
    "captcha_backup_key",
    "captcha_backup_host",
)

_DEFAULTS: dict[str, Any] = {
    "captcha_provider": "none",
    "captcha_key": "",
    "captcha_host": "",
    "captcha_retries": 2,
    "captcha_backup_provider": "none",
    "captcha_backup_key": "",
    "captcha_backup_host": "",
}


def captcha_dict_from(obj: Any | None) -> dict[str, Any]:
    """Extract captcha fields from a settings-like object or dict."""
    out = dict(_DEFAULTS)
    if obj is None:
        return out
    for k in CAPTCHA_KEYS:
        if isinstance(obj, dict):
            if k not in obj or obj[k] is None:
                continue
            out[k] = obj[k]
        elif hasattr(obj, k):
            val = getattr(obj, k)
            if val is not None:
                out[k] = val
    out["captcha_provider"] = str(out.get("captcha_provider") or "none")
    out["captcha_backup_provider"] = str(out.get("captcha_backup_provider") or "none")
    out["captcha_key"] = str(out.get("captcha_key") or "")
    out["captcha_backup_key"] = str(out.get("captcha_backup_key") or "")
    out["captcha_host"] = str(out.get("captcha_host") or "")
    out["captcha_backup_host"] = str(out.get("captcha_backup_host") or "")
    try:
        out["captcha_retries"] = max(0, int(out.get("captcha_retries") or 2))
    except (TypeError, ValueError):
        out["captcha_retries"] = 2
    return out


def captcha_is_configured(obj: Any | None) -> bool:
    """True if primary or backup solver has a non-none provider or a key set."""
    d = captcha_dict_from(obj)
    primary = d["captcha_provider"] != "none" or bool(d["captcha_key"].strip())
    backup = d["captcha_backup_provider"] != "none" or bool(d["captcha_backup_key"].strip())
    return primary or backup


def apply_captcha_to_settings(settings: dict[str, Any], captcha: Any | None) -> dict[str, Any]:
    """Overwrite lease settings captcha keys from global (or fallback) source."""
    for k, v in captcha_dict_from(captcha).items():
        settings[k] = v
    return settings


def _copy_captcha_into(dest: CaptchaSettings, src: Any) -> None:
    data = captcha_dict_from(src)
    for k, v in data.items():
        setattr(dest, k, v)


async def _find_legacy_captcha_source(db: AsyncSession) -> Any | None:
    """Pick first useful legacy captcha config: default profile → any profile → worker_config."""
    profiles = (
        await db.execute(select(ScrapeSettings).order_by(ScrapeSettings.is_default.desc(), ScrapeSettings.id))
    ).scalars().all()
    for p in profiles:
        if captcha_is_configured(p):
            return p
    workers = (await db.execute(select(WorkerNode).order_by(WorkerNode.id))).scalars().all()
    for w in workers:
        cfg = w.worker_config or {}
        if captcha_is_configured(cfg):
            return cfg
    return None


async def ensure_captcha_settings(db: AsyncSession) -> CaptchaSettings:
    """Ensure singleton row exists; one-time copy from legacy profile/worker if empty."""
    row = await db.get(CaptchaSettings, 1)
    created = False
    if not row:
        row = CaptchaSettings(id=1)
        db.add(row)
        await db.flush()
        created = True
    if not captcha_is_configured(row):
        legacy = await _find_legacy_captcha_source(db)
        if legacy is not None:
            _copy_captcha_into(row, legacy)
            await db.commit()
            await db.refresh(row)
            return row
    if created:
        await db.commit()
        await db.refresh(row)
    return row


async def get_captcha_settings(db: AsyncSession) -> CaptchaSettings:
    return await ensure_captcha_settings(db)


def resolve_captcha_for_lease(
    global_captcha: CaptchaSettings | None,
    *,
    scrape_fallback: Any | None = None,
    worker_config_fallback: dict | None = None,
) -> dict[str, Any]:
    """Prefer global settings; fall back to profile then worker_config for mid-migration safety."""
    if captcha_is_configured(global_captcha):
        return captcha_dict_from(global_captcha)
    if captcha_is_configured(scrape_fallback):
        return captcha_dict_from(scrape_fallback)
    if captcha_is_configured(worker_config_fallback):
        return captcha_dict_from(worker_config_fallback)
    return captcha_dict_from(global_captcha)
