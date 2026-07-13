"""Phase F/G — Meta + LinkedIn + X public discovery (browser, no official APIs).

Sources:
  F: facebook_pages | facebook_groups | facebook_posts | facebook_comments | instagram
  G: linkedin | twitter

Heavy login/captcha walls are expected. Primary path = Google SERP with site:
filters; native URLs tried when useful. Yield is best-effort public metadata.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus, urlparse, unquote

from browser_scrape_lib import (
    Unit,
    cartesian_units,
    goto,
    google_search_url,
    load_proxy_list,
    parse_google_organic,
    run_threaded_units,
    write_csv,
)

PHASE_FG_SOURCES = frozenset(
    {
        "facebook_pages",
        "facebook_groups",
        "facebook_posts",
        "facebook_comments",
        "instagram",
        "linkedin",
        "twitter",
    }
)

CSV_COMMON = [
    "keyword",
    "location",
    "title",
    "url",
    "snippet",
    "handle",
    "entity_type",
    "query",
]

# Per-source Google query templates
_SITE_QUERY: dict[str, Callable[[str], str]] = {
    "facebook_pages": lambda q: f'site:facebook.com "{q}" (page OR pages OR "about")',
    "facebook_groups": lambda q: f"site:facebook.com/groups {q}",
    "facebook_posts": lambda q: f"site:facebook.com {q} (posts OR permalink OR story)",
    "facebook_comments": lambda q: f"site:facebook.com {q} (comment OR comments OR replied)",
    "instagram": lambda q: f"site:instagram.com {q}",
    "linkedin": lambda q: f"site:linkedin.com {q} (in OR company OR pulse)",
    "twitter": lambda q: f"(site:x.com OR site:twitter.com) {q}",
}


def _cap(args) -> int:
    n = int(getattr(args, "max_results", 0) or 0)
    return n if n > 0 else 20


def _query(unit: Unit) -> str:
    return f"{unit.keyword} {unit.location}".strip()


def _host_ok(url: str, source: str) -> bool:
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return False
    if source.startswith("facebook"):
        return "facebook.com" in host or "fb.com" in host
    if source == "instagram":
        return "instagram.com" in host
    if source == "linkedin":
        return "linkedin.com" in host
    if source == "twitter":
        return "twitter.com" in host or "x.com" in host
    return True


def _guess_handle(url: str, source: str) -> str:
    try:
        path = unquote(urlparse(url).path or "")
    except Exception:
        return ""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return ""
    if source.startswith("facebook"):
        if parts[0] in ("pages", "groups", "profile.php", "watch", "reel", "photo"):
            return parts[1] if len(parts) > 1 else parts[0]
        return parts[0]
    if source == "instagram":
        if parts[0] in ("p", "reel", "tv", "stories"):
            return ""
        return parts[0].lstrip("@")
    if source == "linkedin":
        if parts[0] in ("in", "company", "school") and len(parts) > 1:
            return parts[1]
        return parts[0]
    if source == "twitter":
        skip = {"i", "intent", "search", "hashtag", "share", "home", "explore"}
        if parts[0] in skip:
            return ""
        return parts[0].lstrip("@")
    return ""


def _entity_type(url: str, source: str) -> str:
    u = (url or "").lower()
    if source == "facebook_pages" or "/pages/" in u:
        return "page"
    if source == "facebook_groups" or "/groups/" in u:
        return "group"
    if source == "facebook_posts" or "/posts/" in u or "permalink" in u or "/story.php" in u:
        return "post"
    if source == "facebook_comments":
        return "comment_thread"
    if source == "instagram":
        if "/p/" in u or "/reel/" in u:
            return "post"
        return "profile"
    if source == "linkedin":
        if "/company/" in u:
            return "company"
        if "/in/" in u:
            return "person"
        return "linkedin"
    if source == "twitter":
        if "/status/" in u:
            return "post"
        return "profile"
    return source


def _filter_url(url: str, source: str) -> bool:
    if not url or not _host_ok(url, source):
        return False
    u = url.lower()
    if source == "facebook_groups":
        return "/groups/" in u
    if source == "facebook_pages":
        # Prefer pages; still allow bare profile-like paths that SERP returns as pages
        if "/groups/" in u or "/marketplace/" in u:
            return False
        return True
    if source == "facebook_posts":
        return any(x in u for x in ("/posts/", "permalink", "/story.php", "/photo", "/watch", "/reel/"))
    if source == "facebook_comments":
        # Threads often live on post URLs; accept posts + comment anchors
        return any(x in u for x in ("/posts/", "permalink", "/story.php", "comment_id", "reply"))
    if source == "instagram":
        return "instagram.com" in u and "/accounts/" not in u
    if source == "linkedin":
        return any(x in u for x in ("/in/", "/company/", "/school/", "/pulse/", "/posts/"))
    if source == "twitter":
        return True
    return True


def _serp_rows(args, unit: Unit, page, source: str) -> list[dict[str, Any]]:
    q = _query(unit)
    gq = _SITE_QUERY[source](q)
    if not goto(page, google_search_url(gq, num=_cap(args)), args):
        return []
    try:
        page.wait_for_timeout(1400)
    except Exception:
        pass
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in parse_google_organic(page):
        url = (item.get("url") or "").strip()
        if not _filter_url(url, source):
            continue
        # Normalize tracking junk
        url = url.split("?")[0] if "facebook.com" in url or "instagram.com" in url else url
        if url in seen:
            continue
        seen.add(url)
        rows.append(
            {
                "keyword": unit.keyword,
                "location": unit.location,
                "title": item.get("title", ""),
                "url": url,
                "snippet": item.get("snippet", ""),
                "handle": _guess_handle(url, source),
                "entity_type": _entity_type(url, source),
                "query": gq,
            }
        )
        if len(rows) >= _cap(args):
            break
    return rows


def _try_native_instagram(args, unit: Unit, page) -> list[dict[str, Any]]:
    """Best-effort Instagram tag/search; usually blocked — returns [] often."""
    q = _query(unit)
    tag = re.sub(r"[^a-zA-Z0-9_]", "", unit.keyword.replace(" ", ""))[:40]
    url = f"https://www.instagram.com/explore/tags/{quote_plus(tag)}/" if tag else (
        f"https://www.instagram.com/explore/search/keyword/?q={quote_plus(q)}"
    )
    if not goto(page, url, args):
        return []
    try:
        page.wait_for_timeout(2000)
    except Exception:
        pass
    try:
        items = page.evaluate(
            """() => {
              const out = [];
              const seen = new Set();
              for (const a of document.querySelectorAll('a[href*="/p/"], a[href^="/"]')) {
                const href = a.href || '';
                if (!href.includes('instagram.com')) continue;
                if (seen.has(href)) continue;
                const path = new URL(href).pathname || '';
                if (path.split('/').filter(Boolean).length < 1) continue;
                seen.add(href);
                out.push({ url: href, title: (a.getAttribute('alt') || a.innerText || '').trim() });
              }
              return out.slice(0, 30);
            }"""
        )
    except Exception:
        return []
    rows = []
    for it in (items or [])[: _cap(args)]:
        u = it.get("url") or ""
        if not _filter_url(u, "instagram"):
            continue
        rows.append(
            {
                "keyword": unit.keyword,
                "location": unit.location,
                "title": it.get("title", ""),
                "url": u,
                "snippet": "",
                "handle": _guess_handle(u, "instagram"),
                "entity_type": _entity_type(u, "instagram"),
                "query": q,
            }
        )
    return rows


def _work_factory(source: str):
    def _work(args, unit: Unit, page) -> list[dict[str, Any]]:
        rows = _serp_rows(args, unit, page, source)
        if source == "instagram" and len(rows) < 3:
            extra = _try_native_instagram(args, unit, page)
            seen = {r["url"] for r in rows}
            for r in extra:
                if r["url"] not in seen:
                    rows.append(r)
                    seen.add(r["url"])
                if len(rows) >= _cap(args):
                    break
        # Soft enrich: open first few URLs for a longer snippet when not login-walled
        enrich_n = min(3, len(rows))
        for i in range(enrich_n):
            try:
                if not goto(page, rows[i]["url"], args):
                    continue
                page.wait_for_timeout(900)
                text = page.inner_text("body")
                if text and len(text) > 40:
                    # Skip obvious login walls
                    low = text.lower()
                    if any(x in low for x in ("log in", "sign up", "create an account", "join linkedin")):
                        rows[i]["snippet"] = (rows[i].get("snippet") or "")[:300]
                        continue
                    rows[i]["snippet"] = (text[:400]).strip()
            except Exception:
                continue
        return rows

    return _work


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
    source: str = "facebook_pages",
) -> tuple[int, int]:
    _ = solver
    source = (source or "facebook_pages").strip().lower()
    if source in ("x", "twitter_x"):
        source = "twitter"
    if source not in PHASE_FG_SOURCES:
        raise ValueError(f"Unsupported Phase F/G source: {source}")
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
        work=_work_factory(source),
        on_progress=on_progress,
        log=log,
    )
    n = write_csv(Path(out_dir) / f"{source}_{ts}.csv", CSV_COMMON, rows)
    log(f"[{source}] wrote {n} rows")
    return n, 0
