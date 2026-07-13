"""Google Search SERP scraper (Phase C).

Input model matches Maps: keywords × locations Cartesian product.
Each unit runs a browser Google query and writes organic results to CSV.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from browser_scrape_lib import (
    Unit,
    build_args,
    cartesian_units,
    goto,
    google_search_url,
    load_proxy_list,
    parse_google_organic,
    run_threaded_units,
    write_csv,
)

CSV_FIELDS = [
    "keyword",
    "location",
    "rank",
    "title",
    "url",
    "snippet",
    "query",
    "dork_mode",
]


def _work(args, unit: Unit, page) -> list[dict[str, Any]]:
    use_dork = str(getattr(args, "use_dork", False)).lower() in ("1", "true", "yes", "on")
    loc = (unit.location or "").strip()
    if use_dork:
        # Keywords file holds full Google dork queries. Location is optional
        # (ignored when "-", "dork", or blank).
        if not loc or loc.lower() in ("-", "n/a", "na", "dork", "none"):
            query = (unit.keyword or "").strip()
        else:
            query = f"{unit.keyword} {loc}".strip()
    else:
        query = f"{unit.keyword} {unit.location}".strip()
    url = google_search_url(query, num=int(getattr(args, "max_results", 0) or 0) or 20)
    if not goto(page, url, args):
        return []
    try:
        page.wait_for_timeout(1500)
    except Exception:
        pass
    organic = parse_google_organic(page)
    max_r = int(getattr(args, "max_results", 0) or 0)
    if max_r > 0:
        organic = organic[:max_r]
    rows = []
    for i, item in enumerate(organic, start=1):
        rows.append(
            {
                "keyword": unit.keyword,
                "location": unit.location,
                "rank": i,
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("snippet", ""),
                "query": query,
                "dork_mode": "yes" if use_dork else "no",
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
    out = Path(out_dir)
    n = write_csv(out / f"google_search_{ts}.csv", CSV_FIELDS, rows)
    return n, 0


def execute_from_settings(
    settings: dict,
    keywords: list[str],
    locations: list[str],
    start: int,
    end: int,
    out_dir: str,
    ts: str,
    stop_event: threading.Event | None = None,
    proxies_path: str = "",
    on_progress=None,
    log=print,
) -> tuple[int, int]:
    args = build_args(settings)
    if proxies_path:
        args.proxies = proxies_path
        args.no_proxy = not bool(Path(proxies_path).read_text(encoding="utf-8").strip()) if Path(proxies_path).exists() else True
    return execute_index_batch(
        args,
        keywords,
        locations,
        start,
        end,
        out_dir,
        ts,
        stop_event or threading.Event(),
        on_progress=on_progress,
        log=log,
    )
