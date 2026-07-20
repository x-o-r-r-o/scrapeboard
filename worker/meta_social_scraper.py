"""Meta + LinkedIn + X public discovery (browser, no official APIs).

Sources:
  facebook_pages | facebook_groups | facebook_posts | facebook_comments | instagram
  linkedin | twitter

Each CSV row includes **name**, **email**, and **phone** when publicly visible
(SERP snippet + page visit). Login/captcha walls often hide contacts — best-effort.
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
    enrich_page_contacts,
    goto,
    google_search_url,
    load_proxy_list,
    parse_google_organic,
    run_threaded_units,
    write_csv,
)
from email_extract import contacts_from_text, guess_display_name

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
    "name",
    "email",
    "phone",
    "title",
    "url",
    "snippet",
    "handle",
    "entity_type",
    "query",
]

_SITE_QUERY: dict[str, Callable[[str], str]] = {
    "facebook_pages": lambda q: f'site:facebook.com "{q}" (page OR pages OR "about")',
    "facebook_groups": lambda q: f"site:facebook.com/groups {q}",
    "facebook_posts": lambda q: f"site:facebook.com {q} (posts OR permalink OR story)",
    "facebook_comments": lambda q: f"site:facebook.com {q} (comment OR comments OR replied)",
    "instagram": lambda q: f"site:instagram.com {q}",
    "linkedin": lambda q: f"site:linkedin.com {q} (in OR company OR pulse)",
    "twitter": lambda q: f"(site:x.com OR site:twitter.com) {q}",
}

# Prefer contact-oriented SERP when hunting emails/phones on profiles
_CONTACT_QUERY: dict[str, Callable[[str], str]] = {
    "facebook_pages": lambda q: f'site:facebook.com "{q}" (email OR contact OR phone OR call OR "@")',
    "instagram": lambda q: f'site:instagram.com "{q}" (email OR contact OR "DM" OR phone OR "@")',
    "linkedin": lambda q: f'site:linkedin.com "{q}" (email OR contact OR phone)',
    "twitter": lambda q: f'(site:x.com OR site:twitter.com) "{q}" (email OR contact OR phone OR bio)',
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
        if "/groups/" in u or "/marketplace/" in u:
            return False
        return True
    if source == "facebook_posts":
        return any(x in u for x in ("/posts/", "permalink", "/story.php", "/photo", "/watch", "/reel/"))
    if source == "facebook_comments":
        return any(x in u for x in ("/posts/", "permalink", "/story.php", "comment_id", "reply"))
    if source == "instagram":
        return "instagram.com" in u and "/accounts/" not in u
    if source == "linkedin":
        return any(x in u for x in ("/in/", "/company/", "/school/", "/pulse/", "/posts/"))
    if source == "twitter":
        return True
    return True


def _profile_url(url: str, source: str, handle: str) -> str:
    """Prefer a profile/page URL over a post URL for contact enrichment."""
    u = (url or "").lower()
    if source == "instagram" and handle and ("/p/" in u or "/reel/" in u or "/tv/" in u):
        return f"https://www.instagram.com/{handle}/"
    if source == "twitter" and handle and "/status/" in u:
        host = "x.com" if "x.com" in u else "twitter.com"
        return f"https://{host}/{handle}"
    if source == "linkedin" and handle:
        if "/in/" in u:
            return f"https://www.linkedin.com/in/{handle}"
        if "/company/" in u:
            return f"https://www.linkedin.com/company/{handle}"
    if source.startswith("facebook") and handle and any(
        x in u for x in ("/posts/", "/permalink", "/story.php", "/photo", "/watch", "/reel/")
    ):
        return f"https://www.facebook.com/{handle}"
    return url


def _blank_row(unit: Unit, *, title: str, url: str, snippet: str, handle: str, entity_type: str, query: str) -> dict[str, Any]:
    return {
        "keyword": unit.keyword,
        "location": unit.location,
        "name": "",
        "email": "",
        "phone": "",
        "title": title,
        "url": url,
        "snippet": snippet,
        "handle": handle,
        "entity_type": entity_type,
        "query": query,
    }


def _apply_snippet_contacts(row: dict[str, Any]) -> None:
    c = contacts_from_text(f"{row.get('title') or ''}\n{row.get('snippet') or ''}")
    if c.get("email"):
        row["email"] = c["email"]
    if c.get("phone"):
        row["phone"] = c["phone"]
    if not row.get("name"):
        row["name"] = guess_display_name(row.get("title"), row.get("handle"))


def _serp_rows(args, unit: Unit, page, source: str, query_fn: Callable[[str], str]) -> list[dict[str, Any]]:
    q = _query(unit)
    gq = query_fn(q)
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
        url = url.split("?")[0] if "facebook.com" in url or "instagram.com" in url else url
        if url in seen:
            continue
        seen.add(url)
        handle = _guess_handle(url, source)
        row = _blank_row(
            unit,
            title=item.get("title", ""),
            url=url,
            snippet=item.get("snippet", ""),
            handle=handle,
            entity_type=_entity_type(url, source),
            query=gq,
        )
        _apply_snippet_contacts(row)
        rows.append(row)
        if len(rows) >= _cap(args):
            break
    return rows


def _try_native_instagram(args, unit: Unit, page) -> list[dict[str, Any]]:
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
        handle = _guess_handle(u, "instagram")
        row = _blank_row(
            unit,
            title=it.get("title", ""),
            url=u,
            snippet="",
            handle=handle,
            entity_type=_entity_type(u, "instagram"),
            query=q,
        )
        rows.append(row)
    return rows


def _enrich_contacts(args, page, rows: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    for row in rows:
        try:
            visit = _profile_url(row.get("url") or "", source, row.get("handle") or "")
            hints = [row.get("name") or "", row.get("title") or "", row.get("handle") or ""]
            info = enrich_page_contacts(page, args, visit, name_hints=hints)
            if info.get("name"):
                row["name"] = info["name"]
            elif not row.get("name"):
                row["name"] = guess_display_name(*hints)
            if info.get("email") and not row.get("email"):
                row["email"] = info["email"]
            if info.get("phone") and not row.get("phone"):
                row["phone"] = info["phone"]
            if info.get("snippet"):
                # Prefer longer public bio over SERP snippet when available
                if len(info["snippet"]) > len(row.get("snippet") or ""):
                    row["snippet"] = info["snippet"]
            _apply_snippet_contacts(row)
        except Exception:
            _apply_snippet_contacts(row)
        for f in ("name", "email", "phone"):
            row.setdefault(f, "")
    return rows


def _merge_rows(primary: list[dict[str, Any]], extra: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen = {r["url"] for r in primary}
    out = list(primary)
    for r in extra:
        if r["url"] in seen:
            # Merge contacts onto existing
            for existing in out:
                if existing["url"] == r["url"]:
                    for k in ("email", "phone", "name", "snippet"):
                        if r.get(k) and not existing.get(k):
                            existing[k] = r[k]
                    break
            continue
        out.append(r)
        seen.add(r["url"])
        if len(out) >= limit:
            break
    return out[:limit]


def _work_factory(source: str):
    def _work(args, unit: Unit, page) -> list[dict[str, Any]]:
        rows = _serp_rows(args, unit, page, source, _SITE_QUERY[source])
        # Extra contact-oriented SERP for profile-heavy sources
        if source in _CONTACT_QUERY and len(rows) < _cap(args):
            extra = _serp_rows(args, unit, page, source, _CONTACT_QUERY[source])
            rows = _merge_rows(rows, extra, _cap(args))
        if source == "instagram" and len(rows) < 3:
            native = _try_native_instagram(args, unit, page)
            rows = _merge_rows(rows, native, _cap(args))
        return _enrich_contacts(args, page, rows, source)

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
    if source in ("x", "twitter_x", "x_twitter", "twitter-x"):
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
    log(f"[{source}] wrote {n} rows (name/email/phone when public)")
    return n, 0
