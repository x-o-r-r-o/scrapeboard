"""Phase E — public social scrapers (browser, no official APIs).

Sources: youtube · reddit · pinterest · tiktok (general, not Shop).

Strategy per keyword × location:
  1. Prefer native public search URL when stable
  2. Fall back to Google SERP site: filters
  3. Parse visible cards / links into CSV rows
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
    enrich_tiktok_profile,
    goto,
    google_search_url,
    load_proxy_list,
    parse_google_organic,
    run_threaded_units,
    tiktok_usernames_from_text,
    write_csv,
)

PHASE_E_SOURCES = frozenset({"youtube", "reddit", "pinterest", "tiktok"})

CSV_BY_SOURCE: dict[str, list[str]] = {
    "youtube": [
        "keyword",
        "location",
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
        "title",
        "url",
        "snippet",
        "query",
    ],
    "tiktok": [
        "keyword",
        "location",
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
                    "title": it.get("title", ""),
                    "url": it.get("url", ""),
                    "channel": it.get("channel", ""),
                    "channel_url": it.get("channel_url", ""),
                    "snippet": "",
                    "query": q,
                }
            )
    if rows:
        return rows
    # Google fallback
    if not goto(page, google_search_url(f"site:youtube.com {q}", num=_cap(args)), args):
        return []
    try:
        page.wait_for_timeout(1200)
    except Exception:
        pass
    for i, item in enumerate(parse_google_organic(page)[: _cap(args)], start=1):
        u = item.get("url") or ""
        if "youtube.com" not in u:
            continue
        rows.append(
            {
                "keyword": unit.keyword,
                "location": unit.location,
                "title": item.get("title", ""),
                "url": u,
                "channel": "",
                "channel_url": "",
                "snippet": item.get("snippet", ""),
                "query": q,
            }
        )
    return rows


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
                    "title": it.get("title", ""),
                    "url": it.get("url", ""),
                    "subreddit": it.get("subreddit", ""),
                    "author": "",
                    "snippet": "",
                    "query": q,
                }
            )
    if rows:
        return rows
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
                "title": item.get("title", ""),
                "url": u,
                "subreddit": sub,
                "author": "",
                "snippet": item.get("snippet", ""),
                "query": q,
            }
        )
    return rows


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
                    "title": it.get("title", ""),
                    "url": it.get("url", ""),
                    "snippet": "",
                    "query": q,
                }
            )
    if rows:
        return rows
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
                "title": item.get("title", ""),
                "url": u,
                "snippet": item.get("snippet", ""),
                "query": q,
            }
        )
    return rows


def _work_tiktok(args, unit: Unit, page) -> list[dict[str, Any]]:
    """General TikTok (profiles/content), distinct from TikTok Shop commerce."""
    q = _query(unit)
    rows: list[dict[str, Any]] = []
    # Native search often heavily bot-gated — Google discovery is primary
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
        # /@user in URL path
        path = urlparse(item.get("url") or "").path
        m = re.match(r"/@([A-Za-z0-9._]{2,64})", path or "")
        if m:
            _add(m.group(1), item.get("url", ""))
    for u in tiktok_usernames_from_text(body):
        _add(u)

    usernames = usernames[: _cap(args)]
    for user in usernames:
        profile = enrich_tiktok_profile(page, args, user)
        rows.append(
            {
                "keyword": unit.keyword,
                "location": unit.location,
                "username": profile.get("username") or user,
                "nickname": profile.get("nickname", ""),
                "followers": profile.get("followers", ""),
                "bio": profile.get("bio", ""),
                "profile_url": profile.get("profile_url") or f"https://www.tiktok.com/@{user}",
                "discovery_url": discovery.get(user, ""),
                "query": q,
            }
        )
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
    if source not in _WORKERS:
        raise ValueError(f"Unsupported Phase E source: {source}")
    units = cartesian_units(list(keywords or []), list(locations or []), int(start), int(end))
    if not units:
        return 0, 0
    proxies = load_proxy_list(getattr(args, "proxies", "") or "", bool(getattr(args, "no_proxy", False)))
    stop = stop_event if stop_event is not None else threading.Event()
    work = _WORKERS[source]
    rows = run_threaded_units(
        args=args,
        units=units,
        proxies=proxies,
        stop_event=stop,
        work=work,
        on_progress=on_progress,
        log=log,
    )
    fields = CSV_BY_SOURCE[source]
    n = write_csv(Path(out_dir) / f"{source}_{ts}.csv", fields, rows)
    log(f"[{source}] wrote {n} rows")
    return n, 0
