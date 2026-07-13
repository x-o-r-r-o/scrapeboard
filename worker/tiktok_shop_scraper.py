"""TikTok Shop creators — Phase B public discovery (browser, no TikTok API).

Discovery path (no Affiliate Center login):
  1. Google Search for niche × region + TikTok Shop creator signals
  2. Collect @usernames / tiktok.com/@profile links from SERP
  3. Optionally open public TikTok profiles for bio / followers / shop hints

Rich GMV analytics require Affiliate session (later phase).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from browser_scrape_lib import (
    Unit,
    build_args,
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

CSV_FIELDS = [
    "keyword",
    "region",
    "name",
    "email",
    "phone",
    "username",
    "nickname",
    "followers",
    "bio",
    "profile_url",
    "shop_signal",
    "discovery_url",
    "query",
]


def _work(args, unit: Unit, page) -> list[dict[str, Any]]:
    region = unit.location
    query = f"{unit.keyword} tiktok shop creator {region}".strip()
    if not goto(page, google_search_url(query, num=20), args):
        return []
    try:
        page.wait_for_timeout(1500)
    except Exception:
        pass
    organic = parse_google_organic(page)
    # Also scan page text for @handles
    try:
        body = page.inner_text("body")
    except Exception:
        body = ""
    usernames: list[str] = []
    seen: set[str] = set()
    discovery_by_user: dict[str, str] = {}

    def _add(user: str, src: str = ""):
        u = user.lower().strip().lstrip("@")
        if not u or u in seen:
            return
        seen.add(u)
        usernames.append(u)
        if src:
            discovery_by_user[u] = src

    for item in organic:
        blob = f"{item.get('url','')} {item.get('title','')} {item.get('snippet','')}"
        for u in tiktok_usernames_from_text(blob):
            _add(u, item.get("url", ""))
    for u in tiktok_usernames_from_text(body):
        _add(u)

    max_r = int(getattr(args, "max_results", 0) or 0)
    if max_r > 0:
        usernames = usernames[:max_r]
    else:
        usernames = usernames[:25]

    rows: list[dict[str, Any]] = []
    for user in usernames:
        profile = enrich_tiktok_profile(page, args, user)
        rows.append(
            {
                "keyword": unit.keyword,
                "region": region,
                "name": profile.get("name") or profile.get("nickname") or user,
                "email": profile.get("email", ""),
                "phone": profile.get("phone", ""),
                "username": profile.get("username") or user,
                "nickname": profile.get("nickname", ""),
                "followers": profile.get("followers", ""),
                "bio": profile.get("bio", ""),
                "profile_url": profile.get("profile_url") or f"https://www.tiktok.com/@{user}",
                "shop_signal": profile.get("shop_signal", ""),
                "discovery_url": discovery_by_user.get(user, ""),
                "query": query,
            }
        )
    # If SERP found nothing usable, still emit a placeholder row for debugging
    if not rows:
        rows.append(
            {
                "keyword": unit.keyword,
                "region": region,
                "name": "",
                "email": "",
                "phone": "",
                "username": "",
                "nickname": "",
                "followers": "",
                "bio": "",
                "profile_url": "",
                "shop_signal": "",
                "discovery_url": "",
                "query": query,
            }
        )
    return rows


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
) -> tuple[int, int]:
    _ = solver
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
        work=_work,
        on_progress=on_progress,
        log=log,
    )
    # Drop empty placeholder rows if we also have real ones for same unit
    real = [r for r in rows if r.get("username")]
    if real:
        rows = real
    n = write_csv(Path(out_dir) / f"tiktok_shop_{ts}.csv", CSV_FIELDS, rows)
    return n, 0
