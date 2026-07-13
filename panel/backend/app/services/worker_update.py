"""Fleet worker update requests (admin → heartbeat/lease → agent).

State lives in WorkerNode.meta["update"] so no schema migration is required.
Workers never receive arbitrary shell — only a fixed update script path.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm.attributes import flag_modified

from app.models import WorkerNode

VALID_STATUSES = frozenset({"idle", "pending", "updating", "success", "failed"})
DEFAULT_REF = "main"

# Agents before this version ignore heartbeat/lease `commands: ["update"]`.
MIN_REMOTE_UPDATE_VERSION = "0.8.0"
# Online + capable agents should ack within this window; otherwise fail pending.
PENDING_ACK_SECONDS = 120
# Offline workers stuck in pending past this → failed (manual upgrade hint).
PENDING_OFFLINE_SECONDS = 180
# Match panel "online" window used in infra._worker_out.
ONLINE_SECONDS = 90

MSG_OLD_AGENT = (
    f"upgrade agent manually (need {MIN_REMOTE_UPDATE_VERSION}+); "
    "remote Update all is ignored by older agents"
)
MSG_OFFLINE_STALE = (
    f"Worker offline or agent < {MIN_REMOTE_UPDATE_VERSION} — update manually once"
)
MSG_NO_ACK = (
    "agent did not acknowledge update in time; "
    f"confirm agent {MIN_REMOTE_UPDATE_VERSION}+ and re-queue"
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_ref(ref: str | None) -> str:
    raw = (ref or "").strip() or DEFAULT_REF
    if raw.lower() == "latest":
        return "latest"
    # Branch / tag / SHA — keep printable and short
    return raw[:128]


def parse_version(version: str | None) -> tuple[int, ...]:
    """Best-effort numeric version tuple; empty/unknown → (0,)."""
    parts: list[int] = []
    for p in re.split(r"[^0-9]+", str(version or "").strip()):
        if p.isdigit():
            parts.append(int(p))
    return tuple(parts) if parts else (0,)


def version_supports_remote_update(version: str | None) -> bool:
    return parse_version(version) >= parse_version(MIN_REMOTE_UPDATE_VERSION)


def worker_is_online(w: WorkerNode, *, now: datetime | None = None) -> bool:
    if not w.last_seen_at:
        return False
    ts = w.last_seen_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - ts).total_seconds() < ONLINE_SECONDS


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def get_update_state(w: WorkerNode) -> dict[str, Any]:
    meta = w.meta if isinstance(w.meta, dict) else {}
    u = meta.get("update") if isinstance(meta.get("update"), dict) else {}
    status = str(u.get("status") or "idle")
    if status not in VALID_STATUSES:
        status = "idle"
    return {
        "status": status,
        "ref": str(u.get("ref") or DEFAULT_REF),
        "message": str(u.get("message") or "")[:2000],
        "requested_at": u.get("requested_at"),
        "started_at": u.get("started_at"),
        "finished_at": u.get("finished_at"),
    }


def _write_update(w: WorkerNode, patch: dict[str, Any]) -> dict[str, Any]:
    meta = dict(w.meta or {})
    cur = dict(meta.get("update") or {}) if isinstance(meta.get("update"), dict) else {}
    cur.update(patch)
    meta["update"] = cur
    w.meta = meta
    if hasattr(w, "_sa_instance_state"):
        flag_modified(w, "meta")
    return get_update_state(w)


def request_update(w: WorkerNode, *, ref: str | None = None, message: str | None = None) -> dict[str, Any]:
    """Mark worker for update on next heartbeat/lease.

    If the reported agent version cannot handle remote updates, fail immediately
    instead of leaving an infinite pending state.
    """
    wanted = normalize_ref(ref)
    msg = (message or "Queued by admin").strip()[:2000] or "Queued by admin"
    if not version_supports_remote_update(w.version or ""):
        now = utcnow_iso()
        return _write_update(
            w,
            {
                "status": "failed",
                "ref": wanted,
                "message": MSG_OLD_AGENT,
                "requested_at": now,
                "started_at": now,
                "finished_at": now,
            },
        )
    return _write_update(
        w,
        {
            "status": "pending",
            "ref": wanted,
            "message": msg,
            "requested_at": utcnow_iso(),
            "started_at": None,
            "finished_at": None,
        },
    )


def clear_update(w: WorkerNode, *, message: str = "Cleared by admin") -> dict[str, Any]:
    """Reset update column to idle (stuck pending/failed after manual upgrade)."""
    return _write_update(
        w,
        {
            "status": "idle",
            "message": (message or "Cleared by admin")[:2000],
            "requested_at": None,
            "started_at": None,
            "finished_at": utcnow_iso(),
        },
    )


def reconcile_update_state(w: WorkerNode, *, now: datetime | None = None) -> dict[str, Any]:
    """Fail stuck pending updates (old agent / offline / no ack).

    Also promotes failed-for-old-agent → success once the worker heartbeats
    with a capable VERSION (manual git pull + service restart).

    Safe to call from admin list enrichment and worker-api paths.
    """
    state = get_update_state(w)
    ver = w.version or ""
    now = now or datetime.now(timezone.utc)

    # Manual upgrade path: code on disk was updated and service restarted.
    if state["status"] == "failed" and version_supports_remote_update(ver):
        msg = state.get("message") or ""
        if (
            MSG_OLD_AGENT in msg
            or MSG_OFFLINE_STALE in msg
            or "manual" in msg.lower()
            or "upgrade" in msg.lower()
        ):
            return _write_update(
                w,
                {
                    "status": "success",
                    "message": f"manual upgrade detected (agent v{ver})",
                    "finished_at": utcnow_iso(),
                },
            )

    if state["status"] != "pending":
        return state

    requested = _parse_iso(state.get("requested_at")) or now
    age = (now - requested).total_seconds()
    online = worker_is_online(w, now=now)

    if not version_supports_remote_update(ver):
        return apply_worker_status_report(
            w,
            status="failed",
            message=MSG_OLD_AGENT,
            ref=state.get("ref"),
        )

    if not online and age >= PENDING_OFFLINE_SECONDS:
        return apply_worker_status_report(
            w,
            status="failed",
            message=MSG_OFFLINE_STALE,
            ref=state.get("ref"),
        )

    if online and age >= PENDING_ACK_SECONDS:
        return apply_worker_status_report(
            w,
            status="failed",
            message=MSG_NO_ACK,
            ref=state.get("ref"),
        )

    return state


def pending_update_for_heartbeat(w: WorkerNode) -> dict[str, Any] | None:
    """If pending and agent can apply it, return payload for heartbeat/lease.

    Re-sending while still pending lets transient failures retry safely.
    Old agents never get the command (status already failed via reconcile).
    """
    reconcile_update_state(w)
    state = get_update_state(w)
    if state["status"] != "pending":
        return None
    if not version_supports_remote_update(w.version or ""):
        return None
    return {"ref": normalize_ref(state.get("ref")), "force": False}


def attach_update_commands(payload: dict[str, Any], w: WorkerNode) -> dict[str, Any]:
    """Merge update command into a worker-api JSON response (heartbeat or lease)."""
    update_cmd = pending_update_for_heartbeat(w)
    commands = list(payload.get("commands") or [])
    if update_cmd:
        if "update" not in commands:
            commands.append("update")
        payload["commands"] = commands
        payload["update"] = update_cmd
    else:
        payload.setdefault("commands", commands)
    return payload


def apply_worker_status_report(
    w: WorkerNode,
    *,
    status: str,
    message: str = "",
    ref: str | None = None,
) -> dict[str, Any]:
    """Worker-reported progress (updating / success / failed)."""
    st = (status or "").strip().lower()
    if st not in ("updating", "success", "failed"):
        st = "failed"
    patch: dict[str, Any] = {
        "status": st,
        "message": (message or "")[:2000],
    }
    if ref is not None and str(ref).strip():
        patch["ref"] = normalize_ref(ref)
    now = utcnow_iso()
    if st == "updating":
        patch.setdefault("started_at", now)
        patch["finished_at"] = None
    else:
        patch["finished_at"] = now
        if not get_update_state(w).get("started_at"):
            patch["started_at"] = now
    return _write_update(w, patch)
