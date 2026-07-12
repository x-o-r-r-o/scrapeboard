"""Shared user permission defaults and helpers."""

from __future__ import annotations

from typing import Any

from app.models import User

DEFAULT_USER_PERMS: dict[str, Any] = {
    "can_run": True,
    "can_stop": True,
    "can_upload_inputs": True,
    "can_download": True,
    "max_threads": 4,
    "allowed_engines": "all",
    "max_upload_mb": 5,
}

ENGINE_OPTIONS = ["chrome", "google-chrome", "edge", "brave", "camoufox"]

PERM_SCHEMA: list[dict[str, Any]] = [
    {"key": "can_run", "label": "Can run jobs", "type": "bool", "group": "jobs"},
    {"key": "can_stop", "label": "Can stop jobs", "type": "bool", "group": "jobs"},
    {"key": "can_upload_inputs", "label": "Can upload inputs", "type": "bool", "group": "jobs"},
    {"key": "can_download", "label": "Can download results", "type": "bool", "group": "jobs"},
    {"key": "max_threads", "label": "Max threads (cap)", "type": "number", "group": "limits", "min": 1, "max": 64},
    {"key": "max_upload_mb", "label": "Max upload MB (fallback)", "type": "number", "group": "limits", "min": 1, "max": 500},
    {
        "key": "allowed_engines",
        "label": "Allowed engines",
        "type": "engines",
        "group": "engines",
        "options": ENGINE_OPTIONS,
    },
]


def effective_perms(user: User) -> dict[str, Any]:
    if user.role == "admin":
        return {
            **DEFAULT_USER_PERMS,
            "can_run": True,
            "can_stop": True,
            "can_upload_inputs": True,
            "can_download": True,
            "max_threads": 999,
            "max_upload_mb": 999,
            "allowed_engines": "all",
        }
    return {**DEFAULT_USER_PERMS, **(user.perms or {})}


def normalize_perms(raw: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(DEFAULT_USER_PERMS)
    if not raw:
        return out
    for k, v in raw.items():
        if k not in DEFAULT_USER_PERMS and k not in ("telegram_user",):
            # allow known keys + telegram_user flag
            if k not in {p["key"] for p in PERM_SCHEMA} and k != "telegram_user":
                continue
        out[k] = v
    # normalize engines
    ae = out.get("allowed_engines", "all")
    if isinstance(ae, list):
        if not ae or "all" in ae:
            out["allowed_engines"] = "all"
        else:
            out["allowed_engines"] = [str(x) for x in ae]
    elif ae not in ("all", None):
        out["allowed_engines"] = str(ae)
    else:
        out["allowed_engines"] = "all"
    try:
        out["max_threads"] = max(1, min(64, int(out.get("max_threads") or 4)))
    except (TypeError, ValueError):
        out["max_threads"] = 4
    try:
        out["max_upload_mb"] = max(1, min(500, int(out.get("max_upload_mb") or 5)))
    except (TypeError, ValueError):
        out["max_upload_mb"] = 5
    for b in ("can_run", "can_stop", "can_upload_inputs", "can_download"):
        out[b] = bool(out.get(b, True))
    return out
