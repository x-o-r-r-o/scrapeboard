"""Shared browser batch runner helpers for non-Maps scrapers.

Reuses gmaps_scraper BrowserSession / proxy / args plumbing so Phase B/C
scrapers stay aligned with the existing worker engine stack.
"""

from __future__ import annotations

import csv
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus


def build_args(settings: dict):
    import gmaps_scraper as gs

    return gs.build_args_from_settings(settings or {})


def load_proxy_list(proxies_path: str, no_proxy: bool) -> list:
    import gmaps_scraper as gs

    if no_proxy or not proxies_path or not os.path.exists(proxies_path):
        return []
    return gs.load_proxies(proxies_path, require_auth=False)


def open_session(args, proxies: list):
    import gmaps_scraper as gs

    proxy = random.choice(proxies) if proxies else None
    return gs.BrowserSession(
        engine=getattr(args, "engine", "chrome") or "chrome",
        proxy=proxy,
        headless=bool(getattr(args, "headless", True)),
        stealth=not bool(getattr(args, "no_stealth", False)),
        browser_path=getattr(args, "browser_path", None) or None,
        geoip=bool(getattr(args, "geoip", False)),
        block_resources=getattr(args, "block_resources", "media") or "media",
    )


def goto(page, url: str, args, log=print) -> bool:
    import gmaps_scraper as gs

    timeout = int(getattr(args, "nav_timeout", 45) or 45)
    ok = gs.robust_goto(page, url, timeout, log)
    if ok:
        try:
            gs.handle_consent(page)
        except Exception:
            pass
    return ok


def sleep_delay(args) -> None:
    lo = float(getattr(args, "min_delay", 2.0) or 2.0)
    hi = float(getattr(args, "max_delay", 5.0) or 5.0)
    if hi < lo:
        hi = lo
    time.sleep(random.uniform(lo, hi))


@dataclass
class Unit:
    keyword: str
    location: str
    index: int


def cartesian_units(keywords: list[str], locations: list[str], start: int, end: int) -> list[Unit]:
    total = max(0, len(keywords) * len(locations))
    end = min(end, total)
    if end <= start or not keywords or not locations:
        return []
    L = len(locations)
    out: list[Unit] = []
    for i in range(start, end):
        out.append(Unit(keywords[i // L], locations[i % L], i))
    return out


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)
    return len(rows)


WorkFn = Callable[[Any, Unit, Any], list[dict[str, Any]]]


def run_threaded_units(
    *,
    args,
    units: list[Unit],
    proxies: list,
    stop_event: threading.Event | None,
    work: WorkFn,
    on_progress=None,
    log=print,
) -> list[dict[str, Any]]:
    """Run work(session_factory_context, unit, page) across threads; return flat rows."""
    if not units:
        return []
    threads = max(1, int(getattr(args, "threads", 1) or 1))
    lock = threading.Lock()
    rows: list[dict[str, Any]] = []
    done = 0

    def _one(unit: Unit) -> list[dict[str, Any]]:
        if stop_event is not None and stop_event.is_set():
            return []
        try:
            with open_session(args, proxies) as session:
                page = session.page
                got = work(args, unit, page) or []
                sleep_delay(args)
                return got
        except Exception as e:
            log(f"[scrape] unit failed keyword={unit.keyword!r} loc={unit.location!r}: {e}")
            return []

    with ThreadPoolExecutor(max_workers=threads) as pool:
        futs = {pool.submit(_one, u): u for u in units}
        for fut in as_completed(futs):
            if stop_event is not None and stop_event.is_set():
                break
            part = fut.result() or []
            with lock:
                rows.extend(part)
                done += 1
                if on_progress:
                    try:
                        on_progress(done, len(rows))
                    except Exception:
                        pass
    return rows


def google_search_url(query: str, *, num: int = 20) -> str:
    return f"https://www.google.com/search?q={quote_plus(query)}&hl=en&num={max(10, min(num, 50))}"


def parse_google_organic(page) -> list[dict[str, str]]:
    """Best-effort organic result parse (selectors change; multiple strategies)."""
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        items = page.evaluate(
            """() => {
              const out = [];
              const blocks = document.querySelectorAll('div.g, div[data-sokoban-container], div.MjjYud');
              for (const b of blocks) {
                const a = b.querySelector('a[href^="http"]');
                if (!a) continue;
                const href = a.href || '';
                if (!href || href.includes('google.com/')) continue;
                const h3 = b.querySelector('h3');
                const title = h3 ? (h3.innerText || '') : (a.innerText || '');
                let snippet = '';
                const sn = b.querySelector('.VwiC3b, .IsZvec, [data-sncf], .s');
                if (sn) snippet = sn.innerText || '';
                out.push({ url: href, title: title.trim(), snippet: snippet.trim() });
              }
              return out;
            }"""
        )
    except Exception:
        items = []
    for it in items or []:
        url = str(it.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        results.append(
            {
                "url": url,
                "title": str(it.get("title") or "").strip(),
                "snippet": str(it.get("snippet") or "").strip(),
            }
        )
    return results


def tiktok_usernames_from_text(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for m in re_tiktok_user().finditer(text or ""):
        user = m.group(1).lower().strip()
        if user and user not in seen and len(user) < 50:
            seen.add(user)
            found.append(user)
    return found


def re_tiktok_user():
    import re

    return re.compile(
        r"(?:https?://(?:www\.)?tiktok\.com/@|@)([A-Za-z0-9._]{2,64})",
        re.I,
    )


def enrich_page_contacts(
    page,
    args,
    url: str,
    *,
    name_hints: list[str] | None = None,
    log=print,
) -> dict[str, str]:
    """Visit a public URL and pull name / email / phone from visible HTML.

    Login walls often hide contact details; still harvest whatever is public
    (mailto/tel links, bios, snippets in meta tags).
    """
    from email_extract import contacts_from_text, guess_display_name

    out = {
        "name": "",
        "email": "",
        "phone": "",
        "emails": "",
        "phones": "",
        "snippet": "",
        "page_title": "",
    }
    if not url or not goto(page, url, args, log=log):
        # Still use hints for name
        out["name"] = guess_display_name(*(name_hints or []))
        return out
    try:
        page.wait_for_timeout(1100)
    except Exception:
        pass
    try:
        data = page.evaluate(
            """() => {
              const metaDesc = document.querySelector('meta[name="description"]');
              const ogTitle = document.querySelector('meta[property="og:title"]');
              const ogDesc = document.querySelector('meta[property="og:description"]');
              const h1 = document.querySelector('h1');
              let html = '';
              try { html = document.documentElement ? document.documentElement.outerHTML : ''; } catch (e) {}
              let text = '';
              try { text = document.body ? document.body.innerText : ''; } catch (e) {}
              return {
                title: document.title || '',
                ogTitle: ogTitle ? (ogTitle.content || '') : '',
                h1: h1 ? (h1.innerText || '').trim() : '',
                description: metaDesc ? (metaDesc.content || '') : '',
                ogDescription: ogDesc ? (ogDesc.content || '') : '',
                text: (text || '').slice(0, 12000),
                html: (html || '').slice(0, 200000)
              };
            }"""
        )
    except Exception:
        data = {}
    title = str((data or {}).get("title") or "")
    og_title = str((data or {}).get("ogTitle") or "")
    h1 = str((data or {}).get("h1") or "")
    desc = str((data or {}).get("description") or "")
    og_desc = str((data or {}).get("ogDescription") or "")
    text = str((data or {}).get("text") or "")
    html = str((data or {}).get("html") or "")
    out["page_title"] = (og_title or title)[:200]
    blob = f"{html}\n{text}\n{desc}\n{og_desc}"
    contacts = contacts_from_text(blob)
    out["email"] = contacts.get("email") or ""
    out["phone"] = contacts.get("phone") or ""
    out["emails"] = contacts.get("emails") or ""
    out["phones"] = contacts.get("phones") or ""
    low = text.lower()
    if any(x in low for x in ("log in", "sign up", "create an account", "join linkedin", "sign in to")):
        # Keep whatever contacts we already got from meta/html; shorten snippet
        out["snippet"] = (desc or og_desc or text[:240]).strip()[:400]
    else:
        out["snippet"] = (text[:400] or desc or og_desc).strip()[:400]
    out["name"] = guess_display_name(
        h1,
        og_title,
        title,
        *(name_hints or []),
    )
    return out


def enrich_tiktok_profile(page, args, username: str, log=print) -> dict[str, str]:
    """Open public TikTok profile; extract name / bio / followers / email / phone."""
    from email_extract import contacts_from_text, guess_display_name

    url = f"https://www.tiktok.com/@{username}"
    out = {
        "username": username,
        "profile_url": url,
        "nickname": "",
        "name": "",
        "followers": "",
        "bio": "",
        "shop_signal": "",
        "email": "",
        "phone": "",
    }
    if not goto(page, url, args, log=log):
        out["name"] = username
        return out
    try:
        page.wait_for_timeout(2500)
    except Exception:
        pass
    try:
        data = page.evaluate(
            """() => {
              const text = document.body ? document.body.innerText : '';
              const metaDesc = document.querySelector('meta[name="description"]');
              const ogTitle = document.querySelector('meta[property="og:title"]');
              let html = '';
              try { html = document.documentElement ? document.documentElement.outerHTML : ''; } catch (e) {}
              return {
                text: (text || '').slice(0, 8000),
                html: (html || '').slice(0, 150000),
                description: metaDesc ? (metaDesc.content || '') : '',
                title: ogTitle ? (ogTitle.content || '') : (document.title || ''),
                hasShop: /tiktok shop|shop tab|view shop/i.test(text || '')
              };
            }"""
        )
    except Exception:
        data = {}
    title = str((data or {}).get("title") or "")
    desc = str((data or {}).get("description") or "")
    text = str((data or {}).get("text") or "")
    html = str((data or {}).get("html") or "")
    out["nickname"] = title.split("@")[0].strip(" |-\n\t")[:120]
    out["name"] = guess_display_name(out["nickname"], title, username) or username
    out["bio"] = (desc or text[:400]).strip()[:500]
    contacts = contacts_from_text(f"{html}\n{text}\n{desc}\n{out['bio']}")
    out["email"] = contacts.get("email") or ""
    out["phone"] = contacts.get("phone") or ""
    import re

    m = re.search(r"([\d.,]+[KMB]?)\s*Followers", text, re.I)
    if m:
        out["followers"] = m.group(1)
    if (data or {}).get("hasShop"):
        out["shop_signal"] = "yes"
    elif re.search(r"tiktok\s*shop|view shop", text, re.I):
        out["shop_signal"] = "yes"
    return out
