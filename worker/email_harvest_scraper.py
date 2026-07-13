"""Email harvest scraper (Phase C) — Google Search channel only.

Flow per keyword × location:
  1. Google SERP for contact/email-oriented query
  2. Visit top organic pages
  3. Extract emails (mailto / regex)
  4. CSV with source URL + channel

Other social channels remain registered but not implemented here.
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
    sleep_delay,
    write_csv,
)
from email_extract import classify_email, extract_emails_from_text

CSV_FIELDS = [
    "email",
    "email_type",
    "source_url",
    "page_title",
    "keyword",
    "location",
    "channel",
    "query",
]

SUPPORTED_CHANNELS = frozenset({"google_search"})


def _visit_and_extract(args, page, url: str, meta: dict[str, str]) -> list[dict[str, Any]]:
    if not goto(page, url, args):
        return []
    try:
        page.wait_for_timeout(1200)
    except Exception:
        pass
    try:
        title = page.title() or ""
    except Exception:
        title = ""
    try:
        html = page.content()
    except Exception:
        html = ""
    try:
        text = page.inner_text("body")
    except Exception:
        text = ""
    emails = extract_emails_from_text(f"{html}\n{text}")
    rows = []
    for email in emails:
        rows.append(
            {
                "email": email,
                "email_type": classify_email(email),
                "source_url": url,
                "page_title": title[:200],
                "keyword": meta.get("keyword", ""),
                "location": meta.get("location", ""),
                "channel": "google_search",
                "query": meta.get("query", ""),
            }
        )
    return rows


def _work(args, unit: Unit, page) -> list[dict[str, Any]]:
    # Intentional contact-oriented SERP query
    query = f'{unit.keyword} {unit.location} email OR contact OR "mailto"'.strip()
    if not goto(page, google_search_url(query, num=15), args):
        return []
    try:
        page.wait_for_timeout(1500)
    except Exception:
        pass
    organic = parse_google_organic(page)
    max_pages = int(getattr(args, "max_results", 0) or 0)
    if max_pages <= 0:
        max_pages = 8
    organic = organic[: max(1, min(max_pages, 15))]

    meta = {"keyword": unit.keyword, "location": unit.location, "query": query}
    rows: list[dict[str, Any]] = []
    seen_email: set[str] = set()

    # Emails sometimes appear in SERP snippets
    for item in organic:
        for email in extract_emails_from_text(
            f"{item.get('title','')} {item.get('snippet','')} {item.get('url','')}"
        ):
            if email in seen_email:
                continue
            seen_email.add(email)
            rows.append(
                {
                    "email": email,
                    "email_type": classify_email(email),
                    "source_url": item.get("url", ""),
                    "page_title": item.get("title", "")[:200],
                    "keyword": unit.keyword,
                    "location": unit.location,
                    "channel": "google_search",
                    "query": query,
                }
            )

    for item in organic:
        url = item.get("url") or ""
        if not url:
            continue
        part = _visit_and_extract(args, page, url, meta)
        for r in part:
            if r["email"] in seen_email:
                continue
            seen_email.add(r["email"])
            rows.append(r)
        sleep_delay(args)
    return rows


def _normalize_channels(raw) -> list[str]:
    if raw is None:
        return ["google_search"]
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split(",") if p.strip()]
    out = []
    for c in raw or []:
        c = str(c).strip().lower()
        if c in SUPPORTED_CHANNELS and c not in out:
            out.append(c)
    return out or ["google_search"]


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
    channels=None,
) -> tuple[int, int]:
    _ = solver
    chans = _normalize_channels(channels)
    unsupported = [c for c in (channels or []) if str(c).strip().lower() not in SUPPORTED_CHANNELS and str(c).strip()]
    for c in unsupported:
        log(f"[email_harvest] channel {c!r} not implemented yet — skipping")
    if "google_search" not in chans:
        log("[email_harvest] no supported channels selected")
        return 0, 0

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
    validate_after = str(getattr(args, "validate_after", False)).lower() in (
        "1",
        "true",
        "yes",
    )
    # Also accept from a custom attribute if set by agent
    if not validate_after and getattr(args, "validate_emails", None):
        validate_after = str(getattr(args, "validate_emails")).lower() in ("1", "true", "yes")

    fields = list(CSV_FIELDS)
    if validate_after and rows:
        try:
            from email_validator import enrich_harvest_rows

            rows = enrich_harvest_rows(
                rows,
                threads=max(1, int(getattr(args, "threads", 4) or 4)),
                check_disposable=True,
                check_mx_flag=True,
                do_smtp=str(getattr(args, "smtp_probe", False)).lower() in ("1", "true", "yes"),
                stop_event=stop,
            )
            for col in (
                "status",
                "reason",
                "syntax_ok",
                "mx_ok",
                "is_disposable",
                "is_role",
                "smtp_ok",
            ):
                if col not in fields:
                    fields.append(col)
            log(f"[email_harvest] validated {len(rows)} harvested emails")
        except Exception as e:
            log(f"[email_harvest] validate_after failed: {e}")

    n = write_csv(Path(out_dir) / f"email_harvest_{ts}.csv", fields, rows)
    return n, 0
