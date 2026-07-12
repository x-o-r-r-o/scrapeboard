"""Default worker scrape flags and merge helpers.

Precedence when a worker leases a job chunk:
  1. Assigned scrape profile (or global default profile)
  2. Per-worker worker_config (Admin → Workers fine-tuning)
  3. Per-job settings (engine/threads/websites/max_results overrides)

WorkerNode.max_browsers caps concurrent job *instances* (leases), not threads
inside a single user instance.
"""

from __future__ import annotations

from typing import Any

# Flags that map onto gmaps_scraper argparse / build_args_from_settings
WORKER_SCRAPE_KEYS: tuple[str, ...] = (
    "engine",
    "threads",
    "block_resources",
    "scrape_websites",
    "max_results",
    "min_delay",
    "max_delay",
    "cooldown_every",
    "cooldown_min",
    "cooldown_max",
    "captcha_provider",
    "captcha_key",
    "captcha_host",
    "captcha_retries",
    "captcha_backup_provider",
    "captcha_backup_key",
    "captcha_backup_host",
    "nav_timeout",
    "proxy_attempts",
    "headless",
    "no_stealth",
    "browser_path",
    "geoip",
    "preflight_timeout",
    "no_preflight",
    "fresh",
    "debug",
)

SECRET_KEYS = ("captcha_key", "captcha_backup_key")

DEFAULT_WORKER_CONFIG: dict[str, Any] = {
    "engine": "chrome",
    "threads": 2,
    "block_resources": "media",
    "scrape_websites": "yes",
    "max_results": 0,
    "min_delay": 2.0,
    "max_delay": 5.0,
    "cooldown_every": 25,
    "cooldown_min": 25.0,
    "cooldown_max": 60.0,
    "captcha_provider": "none",
    "captcha_key": "",
    "captcha_host": "",
    "captcha_retries": 2,
    "captcha_backup_provider": "none",
    "captcha_backup_key": "",
    "captcha_backup_host": "",
    "nav_timeout": 45,
    "proxy_attempts": 3,
    "headless": True,
    "no_stealth": False,
    "browser_path": "",
    "geoip": False,
    "preflight_timeout": 12.0,
    "no_preflight": False,
    "fresh": False,
    "debug": False,
}


def scrape_settings_to_config(scrape: Any | None) -> dict[str, Any]:
    """Build a worker_config dict from a ScrapeSettings profile row."""
    out = dict(DEFAULT_WORKER_CONFIG)
    if scrape is None:
        return out
    for k in WORKER_SCRAPE_KEYS:
        if hasattr(scrape, k):
            val = getattr(scrape, k)
            if val is not None:
                out[k] = val
    return out


def normalize_worker_config(raw: dict | None) -> dict[str, Any]:
    """Fill defaults for missing keys; keep only known scrape keys."""
    base = dict(DEFAULT_WORKER_CONFIG)
    if not raw:
        return base
    for k in WORKER_SCRAPE_KEYS:
        if k not in raw or raw[k] is None:
            continue
        v = raw[k]
        if k in ("browser_path", "captcha_host", "captcha_backup_host"):
            base[k] = str(v or "")
        else:
            base[k] = v
    return base


def merge_lease_settings(
    *,
    scrape: Any | None,
    worker_config: dict | None,
    job_settings: dict | None,
    max_browsers: int | None = None,
) -> dict[str, Any]:
    """Merge profile → worker → job. max_browsers is unused for thread caps."""
    _ = max_browsers  # concurrent instance slots — enforced at lease time
    settings = scrape_settings_to_config(scrape)
    for k, v in (worker_config or {}).items():
        if k in WORKER_SCRAPE_KEYS and v is not None:
            settings[k] = v
    for k, v in (job_settings or {}).items():
        if k in WORKER_SCRAPE_KEYS and v is not None:
            settings[k] = v
    try:
        threads = int(settings.get("threads") or 1)
    except (TypeError, ValueError):
        threads = 1
    settings["threads"] = max(1, min(threads, 64))
    # Empty browser_path → omit so scraper uses engine default
    if not str(settings.get("browser_path") or "").strip():
        settings["browser_path"] = None
    return settings


def public_worker_config(cfg: dict | None) -> dict[str, Any]:
    """API-safe view: hide captcha keys, expose configured flags."""
    data = normalize_worker_config(cfg)
    key = str(data.pop("captcha_key", "") or "")
    backup = str(data.pop("captcha_backup_key", "") or "")
    data["captcha_key_configured"] = bool(key.strip())
    data["captcha_backup_key_configured"] = bool(backup.strip())
    return data


def apply_worker_config_update(existing: dict | None, patch: dict | None) -> dict[str, Any]:
    """Merge PATCH into existing worker_config; blank secret keys mean keep."""
    out = normalize_worker_config(existing)
    if not patch:
        return out
    for k, v in patch.items():
        if k in ("captcha_key_configured", "captcha_backup_key_configured"):
            continue
        if k not in WORKER_SCRAPE_KEYS:
            continue
        if k in SECRET_KEYS:
            if v is None or str(v) == "":
                continue
            out[k] = str(v)
            continue
        if v is None:
            continue
        out[k] = v
    return normalize_worker_config(out)


def copy_profile_fields(src: Any, dest: Any) -> None:
    """Copy scrape flag fields from one ScrapeSettings-like object to another."""
    for k in WORKER_SCRAPE_KEYS:
        if hasattr(src, k) and hasattr(dest, k):
            setattr(dest, k, getattr(src, k))
    if hasattr(src, "chunk_size") and hasattr(dest, "chunk_size"):
        dest.chunk_size = src.chunk_size
