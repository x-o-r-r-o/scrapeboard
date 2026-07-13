"""Public social scrapers (browser, no official APIs).

Sources: youtube · reddit · pinterest · tiktok (general, not Shop).

Each result row includes **name**, **email**, and **phone** when publicly
visible (page visit + SERP/bio text). Login walls often hide contacts.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus, urlparse

from browser_scrape_lib import (
    Unit,
    cartesian_units,
    enrich_page_contacts,
    enrich_tiktok_profile,
    goto,
    google_search_url,
    load_proxy_list,
    parse_google_organic,
    run_threaded_units,
    tiktok_usernames_from_text,
    write_csv,
)
from email_extract import contacts_from_text, guess_display_name

PHASE_E_SOURCES = frozenset({"youtube", "reddit", "pinterest", "tiktok"})

CONTACT_FIELDS = ["name", "email", "phone"]

CSV_BY_SOURCE: dict[str, list[str]] = {
    "youtube": [
        "keyword",
        "location",
        "name",
        "email",
        "phone",
        "title",
        "url",
        "channel",
        "channel_url",
        "snippet",
        "query",
    ],
    "reddit": [
        "keyword",
        "location",
        "name",
        "email",
        "phone",
        "title",
        "url",
        "subreddit",
        "author",
        "snippet",
        "query",
    ],
    "pinterest": [
        "keyword",
        "location",
        "name",
        "email",
        "phone",
        "title",
        "url",
        "snippet",
        "query",
    ],
    "tiktok": [
        "keyword",
        "location",
        "name",
        "email",
        "phone",
        "username",
        "nickname",
        "followers",
        "bio",
        "profile_url",
        "discovery_url",
        "query",
    ],
}


def _cap(args) -> int:
    n = int(getattr(args, "max_results", 0) or 0)
    return n if n > 0 else 20


def _query(unit: Unit) -> str:
    return f"{unit.keyword} {unit.location}".strip()


def _seed_contacts(row: dict[str, Any], *blobs: str) -> None:
    """Fill name/email/phone from free text (snippets) without a page visit."""
    blob = "\n".join(b for b in blobs if b)
    c = contacts_from_text(blob)
    if c.get("email") and not row.get("email"):
        row["email"] = c["email"]
    if c.get("phone") and not row.get("phone"):
        row["phone"] = c["phone"]
    if not row.get("name"):
        row["name"] = guess_display_name(
            row.get("channel"),
            row.get("author"),
            row.get("nickname"),
            row.get("title"),
            row.get("username"),
            row.get("subreddit"),
        )


def _enrich_row(args, page, row: dict[str, Any], *, prefer_url_keys: tuple[str, ...] = ("url",)) -> None:
    _seed_contacts(row, row.get("snippet") or "", row.get("title") or "", row.get("bio") or "")
    visit = ""
    for k in prefer_url_keys:
        visit = (row.get(k) or "").strip()
        if visit:
            break
    if not visit:
        return
    hints = [
        row.get("name") or "",
        row.get("channel") or "",
        row.get("nickname") or "",
        row.get("author") or "",
        row.get("title") or "",
        row.get("username") or "",
        row.get("handle") or "",
    ]
    info = enrich_page_contacts(page, args, visit, name_hints=hints)
    if info.get("name"):
        row["name"] = info["name"]
    elif not row.get("name"):
        row["name"] = guess_display_name(*hints)
    if info.get("email") and not row.get("email"):
        row["email"] = info["email"]
    if info.get("phone") and not row.get("phone"):
        row["phone"] = info["phone"]
    if info.get("snippet") and not row.get("snippet"):
        row["snippet"] = info["snippet"]


def _enrich_all(args, page, rows: list[dict[str, Any]], *, prefer_url_keys: tuple[str, ...] = ("url",)) -> list[dict[str, Any]]:
    for row in rows:
        try:
            _enrich_row(args, page, row, prefer_url_keys=prefer_url_keys)
        except Exception:
            _seed_contacts(row, row.get("snippet") or "", row.get("title") or "")
        for f in CONTACT_FIELDS:
            row.setdefault(f, "")
    return rows


def _work_youtube(args, unit: Unit, page) -> list[dict[str, Any]]:
    q = _query(unit)
    url = f"https://www.youtube.com/results?search_query={quote_plus(q)}"
    rows: list[dict[str, Any]] = []
    if goto(page, url, args):
        try:
            page.wait_for_timeout(2000)
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(800)
        except Exception:
            pass
        try:
            items = page.evaluate(
                """() => {
                  const out = [];
                  const seen = new Set();
                  for (const a of document.querySelectorAll('a#video-title, a.yt-simple-endpoint[href*="/watch"]')) {
                    const href = a.href || '';
                    if (!href.includes('/watch') || seen.has(href)) continue;
                    seen.add(href);
                    const title = (a.getAttribute('title') || a.innerText || '').trim();
                    let channel = '', channelUrl = '';
                    const row = a.closest('ytd-video-renderer, ytd-rich-item-renderer, ytd-compact-video-renderer');
                    if (row) {
                      const ca = row.querySelector('a.yt-simple-endpoint[href*="/@"], a.yt-simple-endpoint[href*="/channel/"]');
                      if (ca) { channel = (ca.innerText || '').trim(); channelUrl = ca.href || ''; }
                    }
                    out.push({ title, url: href, channel, channel_url: channelUrl });
                  }
                  return out;
                }"""
            )
        except Exception:
            items = []
        for it in (items or [])[: _cap(args)]:
            rows.append(
                {
                    "keyword": unit.keyword,
                    "location": unit.location,
                    "name": "",
                    "email": "",
                    "phone": "",
                    "title": it.get("title", ""),
                    "url": it.get("url", ""),
                    "channel": it.get("channel", ""),
                    "channel_url": it.get("channel_url", ""),
                    "snippet": "",
                    "query": q,
                }
            )
    if not rows:
        if not goto(page, google_search_url(f"site:youtube.com {q}", num=_cap(args)), args):
            return []
        try:
            page.wait_for_timeout(1200)
        except Exception:
            pass
        for item in parse_google_organic(page)[: _cap(args)]:
            u = item.get("url") or ""
            if "youtube.com" not in u:
                continue
            rows.append(
                {
                    "keyword": unit.keyword,
                    "location": unit.location,
                    "name": "",
                    "email": "",
                    "phone": "",
                    "title": item.get("title", ""),
                    "url": u,
                    "channel": "",
                    "channel_url": "",
                    "snippet": item.get("snippet", ""),
                    "query": q,
                }
            )
    return _enrich_all(args, page, rows, prefer_url_keys=("channel_url", "url"))


def _work_reddit(args, unit: Unit, page) -> list[dict[str, Any]]:
    q = _query(unit)
    url = f"https://www.reddit.com/search/?q={quote_plus(q)}&type=link"
    rows: list[dict[str, Any]] = []
    if goto(page, url, args):
        try:
            page.wait_for_timeout(2200)
            page.mouse.wheel(0, 1800)
            page.wait_for_timeout(600)
        except Exception:
            pass
        try:
            items = page.evaluate(
                """() => {
                  const out = [];
                  const seen = new Set();
                  for (const a of document.querySelectorAll('a[href*="/comments/"]')) {
                    const href = a.href || '';
                    if (!href.includes('/comments/') || seen.has(href)) continue;
                    if (href.includes('/user/')) continue;
                    seen.add(href);
                    const title = (a.innerText || '').trim();
                    if (!title || title.length < 3) continue;
                    let subreddit = '';
                    const m = href.match(/reddit\\.com\\/r\\/([^/]+)/i);
                    if (m) subreddit = m[1];
                    out.push({ title, url: href, subreddit });
                  }
                  return out;
                }"""
            )
        except Exception:
            items = []
        for it in (items or [])[: _cap(args)]:
            rows.append(
                {
                    "keyword": unit.keyword,
                    "location": unit.location,
                    "name": "",
                    "email": "",
                    "phone": "",
                    "title": it.get("title", ""),
                    "url": it.get("url", ""),
                    "subreddit": it.get("subreddit", ""),
                    "author": "",
                    "snippet": "",
                    "query": q,
                }
            )
    if not rows:
        if not goto(page, google_search_url(f"site:reddit.com {q}", num=_cap(args)), args):
            return []
        try:
            page.wait_for_timeout(1200)
        except Exception:
            pass
        for item in parse_google_organic(page)[: _cap(args)]:
            u = item.get("url") or ""
            if "reddit.com" not in u:
                continue
            sub = ""
            m = re.search(r"reddit\.com/r/([^/]+)", u, re.I)
            if m:
                sub = m.group(1)
            rows.append(
                {
                    "keyword": unit.keyword,
                    "location": unit.location,
                    "name": "",
                    "email": "",
                    "phone": "",
                    "title": item.get("title", ""),
                    "url": u,
                    "subreddit": sub,
                    "author": "",
                    "snippet": item.get("snippet", ""),
                    "query": q,
                }
            )
    return _enrich_all(args, page, rows)


def _work_pinterest(args, unit: Unit, page) -> list[dict[str, Any]]:
    q = _query(unit)
    url = f"https://www.pinterest.com/search/pins/?q={quote_plus(q)}"
    rows: list[dict[str, Any]] = []
    if goto(page, url, args):
        try:
            page.wait_for_timeout(2500)
            page.mouse.wheel(0, 2200)
            page.wait_for_timeout(700)
        except Exception:
            pass
        try:
            items = page.evaluate(
                """() => {
                  const out = [];
                  const seen = new Set();
                  for (const a of document.querySelectorAll('a[href*="/pin/"]')) {
                    const href = a.href || '';
                    if (!href.includes('/pin/') || seen.has(href)) continue;
                    seen.add(href);
                    const title = (a.getAttribute('aria-label') || a.innerText || '').trim();
                    out.push({ title, url: href });
                  }
                  return out;
                }"""
            )
        except Exception:
            items = []
        for it in (items or [])[: _cap(args)]:
            rows.append(
                {
                    "keyword": unit.keyword,
                    "location": unit.location,
                    "name": "",
                    "email": "",
                    "phone": "",
                    "title": it.get("title", ""),
                    "url": it.get("url", ""),
                    "snippet": "",
                    "query": q,
                }
            )
    if not rows:
        if not goto(page, google_search_url(f"site:pinterest.com {q}", num=_cap(args)), args):
            return []
        try:
            page.wait_for_timeout(1200)
        except Exception:
            pass
        for item in parse_google_organic(page)[: _cap(args)]:
            u = item.get("url") or ""
            if "pinterest." not in u:
                continue
            rows.append(
                {
                    "keyword": unit.keyword,
                    "location": unit.location,
                    "name": "",
                    "email": "",
                    "phone": "",
                    "title": item.get("title", ""),
                    "url": u,
                    "snippet": item.get("snippet", ""),
                    "query": q,
                }
            )
    return _enrich_all(args, page, rows)


def _work_tiktok(args, unit: Unit, page) -> list[dict[str, Any]]:
    """General TikTok (profiles/content), distinct from TikTok Shop commerce."""
    q = _query(unit)
    rows: list[dict[str, Any]] = []
    gq = f"site:tiktok.com {q}"
    if not goto(page, google_search_url(gq, num=20), args):
        return []
    try:
        page.wait_for_timeout(1500)
    except Exception:
        pass
    organic = parse_google_organic(page)
    try:
        body = page.inner_text("body")
    except Exception:
        body = ""
    usernames: list[str] = []
    seen: set[str] = set()
    discovery: dict[str, str] = {}

    def _add(user: str, src: str = ""):
        u = user.lower().strip().lstrip("@")
        if not u or u in seen:
            return
        seen.add(u)
        usernames.append(u)
        if src:
            discovery[u] = src

    for item in organic:
        blob = f"{item.get('url','')} {item.get('title','')} {item.get('snippet','')}"
        for u in tiktok_usernames_from_text(blob):
            _add(u, item.get("url", ""))
        path = urlparse(item.get("url") or "").path
        m = re.match(r"/@([A-Za-z0-9._]{2,64})", path or "")
        if m:
            _add(m.group(1), item.get("url", ""))
    for u in tiktok_usernames_from_text(body):
        _add(u)

    usernames = usernames[: _cap(args)]
    for user in usernames:
        profile = enrich_tiktok_profile(page, args, user)
        row = {
            "keyword": unit.keyword,
            "location": unit.location,
            "name": profile.get("name") or profile.get("nickname") or user,
            "email": profile.get("email", ""),
            "phone": profile.get("phone", ""),
            "username": profile.get("username") or user,
            "nickname": profile.get("nickname", ""),
            "followers": profile.get("followers", ""),
            "bio": profile.get("bio", ""),
            "profile_url": profile.get("profile_url") or f"https://www.tiktok.com/@{user}",
            "discovery_url": discovery.get(user, ""),
            "query": q,
        }
        _seed_contacts(row, row.get("bio") or "")
        rows.append(row)
    return rows


_WORKERS: dict[str, Callable] = {
    "youtube": _work_youtube,
    "reddit": _work_reddit,
    "pinterest": _work_pinterest,
    "tiktok": _work_tiktok,
}


def execute_index_batch(
    args,
    keywords,
    locations,
    start,
    end,
    out_dir,
    ts,
    stop_event,
    solver=None,
    log=print,
    on_progress=None,
    source: str = "youtube",
) -> tuple[int, int]:
    _ = solver
    source = (source or "youtube").strip().lower()
    if source not in PHASE_E_SOURCES:
        raise ValueError(f"Unsupported social source: {source}")
    units = cartesian_units(list(keywords or []), list(locations or []), int(start), int(end))
    if not units:
        return 0, 0
    proxies = load_proxy_list(getattr(args, "proxies", "") or "", bool(getattr(args, "no_proxy", False)))
    stop = stop_event if stop_event is not None else threading.Event()
    rows = run_threaded_units(
        args=args,
        units=units,
        proxies=proxies,
        stop_event=stop,
        work=_WORKERS[source],
        on_progress=on_progress,
        log=log,
    )
    fields = CSV_BY_SOURCE[source]
    n = write_csv(Path(out_dir) / f"{source}_{ts}.csv", fields, rows)
    log(f"[{source}] wrote {n} rows")
    return n, 0
