"""Fleet worker update requests (admin → heartbeat → agent).

State lives in WorkerNode.meta["update"] so no schema migration is required.
Workers never receive arbitrary shell — only a fixed update script path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models import WorkerNode

VALID_STATUSES = frozenset({"idle", "pending", "updating", "success", "failed"})
DEFAULT_REF = "main"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_ref(ref: str | None) -> str:
    raw = (ref or "").strip() or DEFAULT_REF
    if raw.lower() == "latest":
        return "latest"
    # Branch / tag / SHA — keep printable and short
    return raw[:128]


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
    return get_update_state(w)


def request_update(w: WorkerNode, *, ref: str | None = None) -> dict[str, Any]:
    """Mark worker for update on next heartbeat."""
    wanted = normalize_ref(ref)
    return _write_update(
        w,
        {
            "status": "pending",
            "ref": wanted,
            "message": "Queued by admin",
            "requested_at": utcnow_iso(),
            "started_at": None,
            "finished_at": None,
        },
    )


def pending_update_for_heartbeat(w: WorkerNode) -> dict[str, Any] | None:
    """If pending, return payload for the heartbeat response (status stays pending).

    The worker reports updating/success/failed via /worker-api/update-status.
    Re-sending while still pending lets offline or older agents retry safely.
    """
    state = get_update_state(w)
    if state["status"] != "pending":
        return None
    return {"ref": normalize_ref(state.get("ref")), "force": False}


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
