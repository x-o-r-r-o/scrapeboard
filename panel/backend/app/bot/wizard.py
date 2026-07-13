"""Telegram button-driven run wizard (inline keyboards).

Users pick scrapers and options without typing /run. Advanced users still use
typed `/run key=value…`. Callbacks use the `w:` prefix (≤64 bytes).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Package, User
from app.services import billing as billing_svc
from app.services import jobs as jobs_svc
from app.services.notify import edit_text, send_text
from app.services.scraper_registry import (
    GROUP_COMMERCE,
    GROUP_FACEBOOK,
    GROUP_MAPS,
    GROUP_SEARCH_EMAIL,
    GROUP_SOCIAL,
    SOURCE_GMAPS,
    catalog_payload,
    get_scraper,
    normalize_source,
)
from app.services.scraper_settings import get_scraper_settings

log = logging.getLogger("bot.wizard")

# Steps
STEP_GROUPS = "groups"
STEP_SOURCES = "sources"
STEP_OPTIONS = "options"
STEP_UPLOAD_KW = "upload_kw"
STEP_UPLOAD_LOC = "upload_loc"
STEP_CONFIRM = "confirm"

GROUP_EMOJI = {
    GROUP_MAPS: "🗺️",
    GROUP_COMMERCE: "🛒",
    GROUP_SEARCH_EMAIL: "🔎",
    GROUP_FACEBOOK: "📘",
    GROUP_SOCIAL: "📱",
}

# Short aliases for callback_data (keep under 64 bytes)
TOGGLE_KEYS = {
    "sw": "scrape_websites",  # yes/no
    "dk": "use_dork",
    "va": "validate_after",
    "mx": "check_mx",
    "dp": "check_disposable",
    "sm": "smtp_probe",
}
TOGGLE_REVERSE = {v: k for k, v in TOGGLE_KEYS.items()}

ENGINE_CYCLE = ("chrome", "brave", "camoufox")  # unused in Telegram UI — engine fixed to chrome
MAX_RESULTS_CYCLE = (0, 25, 50, 100, 200)
THREAD_PRESETS = (1, 2, 4, 8)


@dataclass
class WizardSession:
    step: str = STEP_GROUPS
    group: str | None = None
    source: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    chat_id: int | None = None
    message_id: int | None = None
    awaiting_upload: str | None = None  # "keywords" | "locations" | None
    max_threads: int = 2


# user.id -> session
_sessions: dict[int, WizardSession] = {}


def get_session(user_id: int) -> WizardSession | None:
    return _sessions.get(user_id)


def clear_session(user_id: int) -> None:
    _sessions.pop(user_id, None)


def _btn(text: str, data: str) -> dict[str, str]:
    return {"text": text[:64], "callback_data": data[:64]}


def _inline(rows: list[list[dict[str, str]]]) -> dict:
    return {"inline_keyboard": rows}


def _yes_no(val: Any) -> bool:
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _opt_bool(opts: dict[str, Any], key: str, default: bool = True) -> bool:
    if key not in opts:
        return default
    return _yes_no(opts[key])


def default_options(source: str, *, max_threads: int = 2) -> dict[str, Any]:
    threads = max(1, min(2, max_threads))
    src = normalize_source(source)
    base: dict[str, Any] = {"threads": threads, "engine": "chrome"}
    if src == SOURCE_GMAPS:
        base["scrape_websites"] = "yes"
    elif src == "google_search":
        base["use_dork"] = "no"
        base["max_results"] = 0
    elif src == "email_harvest":
        base["validate_after"] = "no"
        base["channels"] = "google_search"
    elif src == "email_validate":
        base["check_mx"] = "yes"
        base["check_disposable"] = "yes"
        base["smtp_probe"] = "no"
    elif src in ("youtube", "tiktok_shop", "reddit", "pinterest"):
        base["max_results"] = 50
    return base


def location_optional(source: str, options: dict[str, Any]) -> bool:
    src = normalize_source(source)
    if src == "email_validate":
        return True
    if src == "google_search" and _opt_bool(options, "use_dork", False):
        return True
    return False


def needs_locations(source: str, options: dict[str, Any]) -> bool:
    return not location_optional(source, options)


async def _user_max_threads(db: AsyncSession, user: User) -> int:
    if user.role == "admin":
        return 12
    sub = await billing_svc.active_subscription(db, user)
    if sub and getattr(sub, "threads", None):
        return max(1, int(sub.threads))
    return 2


async def _selectable_rows(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    site = await get_scraper_settings(db)
    is_admin = user.role == "admin"
    allowed: list[str] | None = None
    if not is_admin:
        sub = await billing_svc.active_subscription(db, user)
        if not sub or not getattr(sub, "package_id", None):
            allowed = [SOURCE_GMAPS]
        else:
            pkg = await db.get(Package, sub.package_id)
            allowed = list(getattr(pkg, "allowed_sources", None) or [SOURCE_GMAPS]) if pkg else [SOURCE_GMAPS]
    rows = catalog_payload(
        enabled_sources=list(site.enabled_sources or []),
        allowed_sources=allowed,
        is_admin=is_admin,
    )
    return [r for r in rows if r.get("selectable")]


def _groups_from_rows(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Return (group_id, group_label) in first-seen order."""
    seen: list[tuple[str, str]] = []
    have: set[str] = set()
    for r in rows:
        g = str(r.get("group") or "")
        if not g or g in have:
            continue
        have.add(g)
        seen.append((g, str(r.get("group_label") or g)))
    return seen


def _mark(on: bool) -> str:
    """On = check, off = cross (⬜ often renders blank on Telegram clients)."""
    return "✅" if on else "❌"


def _options_keyboard(sess: WizardSession) -> dict:
    src = normalize_source(sess.source)
    opts = sess.options
    rows: list[list[dict[str, str]]] = []

    if src == SOURCE_GMAPS:
        on = _opt_bool(opts, "scrape_websites", True)
        rows.append([_btn(f"{_mark(on)} Visit websites (email/social)", "w:t:sw")])
    if src == "google_search":
        on = _opt_bool(opts, "use_dork", False)
        rows.append([_btn(f"{_mark(on)} Google dork mode", "w:t:dk")])
        mr = int(opts.get("max_results") or 0)
        label = "unlimited" if mr <= 0 else str(mr)
        rows.append([_btn(f"📊 Max results: {label}", "w:c:mr")])
    if src == "email_harvest":
        on = _opt_bool(opts, "validate_after", False)
        rows.append([_btn(f"{_mark(on)} Validate emails after harvest", "w:t:va")])
    if src == "email_validate":
        rows.append(
            [
                _btn(f"{_mark(_opt_bool(opts, 'check_mx', True))} MX check", "w:t:mx"),
                _btn(f"{_mark(_opt_bool(opts, 'check_disposable', True))} Disposable", "w:t:dp"),
            ]
        )
        rows.append([_btn(f"{_mark(_opt_bool(opts, 'smtp_probe', False))} SMTP probe", "w:t:sm")])
    if src in ("youtube", "tiktok_shop", "reddit", "pinterest") or src.startswith("facebook"):
        if "max_results" in opts or src in ("youtube", "tiktok_shop", "reddit", "pinterest"):
            mr = int(opts.get("max_results") or 50)
            label = "unlimited" if mr <= 0 else str(mr)
            rows.append([_btn(f"📊 Max results: {label}", "w:c:mr")])

    # Threads only — engine is fixed (chrome); not shown in Telegram UI
    th = int(opts.get("threads") or 1)
    rows.append([_btn(f"🧵 Threads: {th} (tap to cycle)", "w:c:th")])

    rows.append([_btn("➡️ Continue", "w:n"), _btn("❌ Cancel", "w:x")])
    rows.append([_btn("⬅️ Back", "w:b")])
    return _inline(rows)


def _options_text(sess: WizardSession) -> str:
    spec = get_scraper(sess.source)
    label = spec.label if spec else sess.source
    lines = [
        f"⚙️ {label}",
        "",
        "Tap toggles to change options, then Continue.",
        "Advanced users can still type: /run source=… key=value",
        "",
    ]
    hide = {"channels", "engine"}
    for k, v in sorted(sess.options.items()):
        if k in hide:
            continue
        lines.append(f"• {k} = {v}")
    return "\n".join(lines)


def _inputs_status(inputs: dict[str, Path] | None, source: str, options: dict[str, Any]) -> tuple[bool, bool]:
    """Return (has_keywords, has_locations_or_ok)."""
    inputs = inputs or {}
    has_kw = bool(inputs.get("keywords") and inputs["keywords"].exists())
    if location_optional(source, options):
        return has_kw, True
    has_loc = bool(inputs.get("locations") and inputs["locations"].exists())
    return has_kw, has_loc


def _upload_kw_keyboard(*, source: str, options: dict[str, Any] | None = None) -> dict:
    src = normalize_source(source)
    options = options or {}
    if src == "email_validate":
        waiting = "📎 Waiting for emails.txt…"
    elif src == "google_search" and _opt_bool(options, "use_dork", False):
        waiting = "📎 Waiting for keywords.txt (dork queries)…"
    else:
        waiting = "📎 Waiting for keywords.txt…"
    return _inline(
        [
            [_btn(waiting, "w:noop")],
            [_btn("➡️ I uploaded — Continue", "w:n"), _btn("❌ Cancel", "w:x")],
            [_btn("⬅️ Back", "w:b")],
        ]
    )


def _upload_loc_keyboard(*, can_skip: bool) -> dict:
    rows: list[list[dict[str, str]]] = [
        [_btn("📎 Waiting for locations.txt…", "w:noop")],
    ]
    cont = [_btn("➡️ I uploaded — Continue", "w:n")]
    if can_skip:
        cont.append(_btn("⏭ Skip locations", "w:sk"))
    rows.append(cont)
    rows.append([_btn("❌ Cancel", "w:x"), _btn("⬅️ Back", "w:b")])
    return _inline(rows)


def _confirm_keyboard() -> dict:
    return _inline(
        [
            [_btn("🚀 Start job", "w:go")],
            [_btn("⬅️ Options", "w:b"), _btn("❌ Cancel", "w:x")],
        ]
    )


def _confirm_text(sess: WizardSession, inputs: dict[str, Path] | None) -> str:
    spec = get_scraper(sess.source)
    label = spec.label if spec else sess.source
    inputs = inputs or {}
    kw = inputs.get("keywords")
    loc = inputs.get("locations")
    lines = [
        f"🚀 Ready to run: {label}",
        f"source={normalize_source(sess.source)}",
        "",
    ]
    hide = {"channels", "engine"}
    for k, v in sorted(sess.options.items()):
        if k in hide:
            continue
        lines.append(f"• {k}={v}")
    lines.append("")
    lines.append(f"keywords.txt: {kw.name if kw and kw.exists() else '—'}")
    if needs_locations(sess.source or "", sess.options):
        lines.append(f"locations.txt: {loc.name if loc and loc.exists() else '—'}")
    else:
        lines.append("locations.txt: not required")
    lines.append("")
    lines.append("Tap Start job — no typing needed.")
    return "\n".join(lines)


async def _render(
    token: str,
    sess: WizardSession,
    text: str,
    markup: dict,
) -> None:
    if sess.chat_id is None:
        return
    if sess.message_id is not None:
        ok = await edit_text(token, sess.chat_id, sess.message_id, text, reply_markup=markup)
        if ok:
            return
    mid = await send_text(token, sess.chat_id, text, reply_markup=markup)
    if mid is not None:
        sess.message_id = mid


async def open_wizard(
    db: AsyncSession,
    token: str,
    chat_id: int,
    user: User,
    *,
    prefer_source: str | None = None,
) -> None:
    """Start or refresh the scraper picker / options wizard."""
    rows = await _selectable_rows(db, user)
    if not rows:
        await send_text(
            token,
            chat_id,
            "No scrapers available on your plan yet. Tap Buy to subscribe, or ask support.",
        )
        return

    max_th = await _user_max_threads(db, user)
    sess = WizardSession(chat_id=chat_id, max_threads=max_th)
    _sessions[user.id] = sess

    if prefer_source:
        want = normalize_source(prefer_source)
        if any(r["id"] == want for r in rows):
            sess.source = want
            sess.options = default_options(want, max_threads=max_th)
            sess.step = STEP_OPTIONS
            await _show_options(token, sess)
            return

    groups = _groups_from_rows(rows)
    if len(groups) == 1:
        sess.group = groups[0][0]
        sess.step = STEP_SOURCES
        await _show_sources(token, sess, rows)
        return

    sess.step = STEP_GROUPS
    await _show_groups(token, sess, groups)


async def _show_groups(token: str, sess: WizardSession, groups: list[tuple[str, str]]) -> None:
    rows_btn: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []
    for gid, glabel in groups:
        emoji = GROUP_EMOJI.get(gid, "•")
        row.append(_btn(f"{emoji} {glabel}", f"w:g:{gid}"))
        if len(row) >= 2:
            rows_btn.append(row)
            row = []
    if row:
        rows_btn.append(row)
    rows_btn.append([_btn("❌ Cancel", "w:x")])
    text = (
        "🛠 Choose a category\n\n"
        "Pick a scraper group, then a source. "
        "Everything after that is buttons — no /run typing required.\n"
        "(Advanced: /run source=… still works.)"
    )
    await _render(token, sess, text, _inline(rows_btn))


async def _show_sources(token: str, sess: WizardSession, all_rows: list[dict[str, Any]]) -> None:
    group = sess.group
    subset = [r for r in all_rows if r.get("group") == group] if group else list(all_rows)
    if not subset:
        subset = list(all_rows)
    rows_btn: list[list[dict[str, str]]] = []
    for r in subset:
        sid = str(r["id"])
        label = str(r.get("label") or sid)
        rows_btn.append([_btn(f"▶️ {label}", f"w:s:{sid}")])
    nav = [_btn("⬅️ Categories", "w:b"), _btn("❌ Cancel", "w:x")]
    rows_btn.append(nav)
    glabel = subset[0].get("group_label") if subset else "Scrapers"
    text = f"🛠 {glabel}\n\nTap a scraper to configure options."
    await _render(token, sess, text, _inline(rows_btn))


async def _show_options(token: str, sess: WizardSession) -> None:
    sess.step = STEP_OPTIONS
    sess.awaiting_upload = None
    await _render(token, sess, _options_text(sess), _options_keyboard(sess))


async def _show_upload_kw(token: str, sess: WizardSession) -> None:
    sess.step = STEP_UPLOAD_KW
    sess.awaiting_upload = "keywords"
    spec = get_scraper(sess.source)
    src = normalize_source(sess.source)
    if src == "email_validate":
        text = (
            "📎 Step 1/1 — upload emails.txt\n\n"
            f"Scraper: {spec.label if spec else src}\n\n"
            "Send a .txt or .csv document.\n"
            "• Caption (or filename): emails\n"
            "• One email per line\n\n"
            "Then tap Continue."
        )
    elif src == "google_search" and _opt_bool(sess.options, "use_dork", False):
        text = (
            "📎 Step 1/1 — upload keywords.txt (Google dorks)\n\n"
            f"Scraper: {spec.label if spec else src}\n\n"
            "Send a .txt or .csv document.\n"
            "• Caption (or filename): keywords or dork\n"
            "• One full Google query per line (site:, filetype:, …)\n"
            "• Locations not required in dork mode\n\n"
            "Then tap Continue."
        )
    else:
        # Maps and most scrapers: keywords.txt then locations.txt
        text = (
            "📎 Step 1/2 — upload keywords.txt\n\n"
            f"Scraper: {spec.label if spec else src}\n\n"
            "Send a .txt or .csv document.\n"
            "• Caption (or filename): keywords\n"
            "• One search keyword / niche per line\n\n"
            "Next you will upload locations.txt.\n"
            "Then tap Continue."
        )
    await _render(token, sess, text, _upload_kw_keyboard(source=src, options=sess.options))


async def _show_upload_loc(token: str, sess: WizardSession) -> None:
    sess.step = STEP_UPLOAD_LOC
    sess.awaiting_upload = "locations"
    can_skip = location_optional(sess.source or "", sess.options)
    spec = get_scraper(sess.source)
    text = (
        "📎 Step 2/2 — upload locations.txt\n\n"
        f"Scraper: {spec.label if spec else sess.source}\n\n"
        "Send a .txt or .csv document.\n"
        "• Caption (or filename): locations (or region)\n"
        "• One location per line (prefer city,state,country)\n"
        "  Example: Austin,Texas,USA\n\n"
        "Then tap Continue."
    )
    if can_skip:
        text += "\n\nLocations are optional for this mode — you can Skip."
    await _render(token, sess, text, _upload_loc_keyboard(can_skip=can_skip))


async def _show_confirm(token: str, sess: WizardSession, inputs: dict[str, Path] | None) -> None:
    sess.step = STEP_CONFIRM
    sess.awaiting_upload = None
    await _render(token, sess, _confirm_text(sess, inputs), _confirm_keyboard())


async def handle_callback(
    db: AsyncSession,
    token: str,
    chat_id: int,
    message_id: int | None,
    user: User,
    data: str,
    inputs: dict[str, Path] | None,
    *,
    answer: Callable[[str], Awaitable[None]],
    start_job: Callable[..., Awaitable[None]],
) -> bool:
    """Handle w:* callback. Returns True if consumed."""
    if not data.startswith("w:"):
        return False

    action = data[2:]  # after w:
    sess = _sessions.get(user.id)
    if message_id is not None and sess:
        sess.message_id = message_id
        sess.chat_id = chat_id

    if action == "noop":
        await answer("Send the file as a document")
        return True

    if action == "x":
        clear_session(user.id)
        await answer("Cancelled")
        if sess and sess.message_id and sess.chat_id:
            await edit_text(
                token,
                sess.chat_id,
                sess.message_id,
                "Cancelled. Tap 🚀 Run anytime to start again.",
                reply_markup={"inline_keyboard": []},
            )
        else:
            await send_text(token, chat_id, "Cancelled. Tap 🚀 Run anytime.")
        return True

    # Ensure session for navigation from stale messages
    rows = await _selectable_rows(db, user)
    max_th = await _user_max_threads(db, user)
    if sess is None:
        sess = WizardSession(chat_id=chat_id, message_id=message_id, max_threads=max_th)
        _sessions[user.id] = sess

    if action.startswith("g:"):
        gid = action[2:]
        sess.group = gid
        sess.step = STEP_SOURCES
        await answer("OK")
        await _show_sources(token, sess, rows)
        return True

    if action.startswith("s:"):
        sid = normalize_source(action[2:])
        if not any(r["id"] == sid for r in rows):
            await answer("Not available")
            return True
        sess.source = sid
        sess.options = default_options(sid, max_threads=max_th)
        sess.max_threads = max_th
        await answer(get_scraper(sid).label if get_scraper(sid) else sid)
        await _show_options(token, sess)
        return True

    if action.startswith("t:"):
        short = action[2:]
        key = TOGGLE_KEYS.get(short)
        if not key:
            await answer("Unknown")
            return True
        cur = _opt_bool(sess.options, key, False)
        sess.options[key] = "no" if cur else "yes"
        await answer("Updated")
        await _show_options(token, sess)
        return True

    if action.startswith("c:"):
        what = action[2:]
        if what == "th":
            cur = int(sess.options.get("threads") or 1)
            presets = [t for t in THREAD_PRESETS if t <= sess.max_threads] or [1]
            try:
                idx = presets.index(cur)
            except ValueError:
                idx = 0
            sess.options["threads"] = presets[(idx + 1) % len(presets)]
        elif what == "mr":
            cur = int(sess.options.get("max_results") or 0)
            try:
                idx = MAX_RESULTS_CYCLE.index(cur)
            except ValueError:
                idx = 0
            sess.options["max_results"] = MAX_RESULTS_CYCLE[(idx + 1) % len(MAX_RESULTS_CYCLE)]
        # "en" (engine) intentionally ignored — Telegram UI does not expose engine
        await answer("Updated")
        await _show_options(token, sess)
        return True

    if action == "b":
        await answer("Back")
        if sess.step == STEP_CONFIRM:
            await _show_options(token, sess)
        elif sess.step in (STEP_UPLOAD_KW, STEP_UPLOAD_LOC):
            await _show_options(token, sess)
        elif sess.step == STEP_OPTIONS:
            sess.step = STEP_SOURCES
            await _show_sources(token, sess, rows)
        elif sess.step == STEP_SOURCES:
            groups = _groups_from_rows(rows)
            sess.step = STEP_GROUPS
            await _show_groups(token, sess, groups)
        else:
            await _show_groups(token, sess, _groups_from_rows(rows))
        return True

    if action == "sk":
        # Skip locations when optional
        if not location_optional(sess.source or "", sess.options):
            await answer("Locations required")
            return True
        await answer("Skipped")
        await _show_confirm(token, sess, inputs)
        return True

    if action == "n":
        # Continue through upload steps
        if sess.step == STEP_OPTIONS:
            await answer("OK")
            has_kw, has_loc = _inputs_status(inputs, sess.source or "", sess.options)
            if not has_kw:
                await _show_upload_kw(token, sess)
                return True
            if needs_locations(sess.source or "", sess.options) and not has_loc:
                await _show_upload_loc(token, sess)
                return True
            await _show_confirm(token, sess, inputs)
            return True
        if sess.step == STEP_UPLOAD_KW:
            has_kw, _ = _inputs_status(inputs, sess.source or "", sess.options)
            if not has_kw:
                await answer("Upload keywords first")
                await _show_upload_kw(token, sess)
                return True
            await answer("OK")
            if needs_locations(sess.source or "", sess.options):
                has_loc = bool(inputs and inputs.get("locations") and inputs["locations"].exists())
                if not has_loc:
                    await _show_upload_loc(token, sess)
                    return True
            await _show_confirm(token, sess, inputs)
            return True
        if sess.step == STEP_UPLOAD_LOC:
            has_kw, has_loc = _inputs_status(inputs, sess.source or "", sess.options)
            if not has_kw:
                await answer("Need keywords")
                await _show_upload_kw(token, sess)
                return True
            if needs_locations(sess.source or "", sess.options) and not has_loc:
                await answer("Upload locations first")
                await _show_upload_loc(token, sess)
                return True
            await answer("OK")
            await _show_confirm(token, sess, inputs)
            return True
        await answer("OK")
        await _show_options(token, sess)
        return True

    if action == "go":
        if not sess.source:
            await answer("Pick a scraper first")
            return True
        has_kw, has_loc = _inputs_status(inputs, sess.source, sess.options)
        if not has_kw:
            await answer("Need keywords file")
            await _show_upload_kw(token, sess)
            return True
        if needs_locations(sess.source, sess.options) and not has_loc:
            await answer("Need locations file")
            await _show_upload_loc(token, sess)
            return True
        await answer("Starting…")
        overrides = {k: str(v) for k, v in sess.options.items()}
        source = sess.source
        clear_session(user.id)
        if sess.message_id and sess.chat_id:
            await edit_text(
                token,
                sess.chat_id,
                sess.message_id,
                f"🚀 Starting {get_scraper(source).label if get_scraper(source) else source}…",
                reply_markup={"inline_keyboard": []},
            )
        await start_job(source=source, overrides=overrides)
        return True

    await answer("Unknown")
    return True


async def on_upload_received(
    token: str,
    user: User,
    inputs: dict[str, Path] | None,
    *,
    kind: str,
) -> None:
    """Refresh wizard screen after a guided upload."""
    sess = _sessions.get(user.id)
    if not sess or not sess.chat_id:
        return
    if sess.step == STEP_UPLOAD_KW and kind in ("keywords",):
        has_kw, has_loc = _inputs_status(inputs, sess.source or "", sess.options)
        if has_kw and needs_locations(sess.source or "", sess.options) and not has_loc:
            await _show_upload_loc(token, sess)
        elif has_kw:
            await _show_confirm(token, sess, inputs)
        else:
            await _show_upload_kw(token, sess)
        return
    if sess.step == STEP_UPLOAD_LOC and kind == "locations":
        await _show_confirm(token, sess, inputs)
        return
    # If confirming and files updated, refresh summary
    if sess.step == STEP_CONFIRM:
        await _show_confirm(token, sess, inputs)


def build_run_overrides(options: dict[str, Any]) -> dict[str, Any]:
    """Normalize wizard options for create_job_from_bytes."""
    out: dict[str, Any] = {}
    for k, v in options.items():
        if k == "channels":
            out[k] = [p.strip() for p in str(v).split(",") if p.strip()]
            continue
        out[k] = v
    return out


# Re-export helpers used by runtime job starter
__all__ = [
    "WizardSession",
    "clear_session",
    "get_session",
    "open_wizard",
    "handle_callback",
    "on_upload_received",
    "build_run_overrides",
    "needs_locations",
    "location_optional",
]
