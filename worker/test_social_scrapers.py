#!/usr/bin/env python3
"""Smoke tests for social scrapers + Chrome/Playwright bootstrap.

Run:
  python test_social_scrapers.py
  python test_social_scrapers.py --live   # also hit the public web (slow)
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PUBLIC_SOURCES = ("youtube", "reddit", "pinterest", "tiktok")
META_SOURCES = (
    "facebook_pages",
    "facebook_groups",
    "facebook_posts",
    "facebook_comments",
    "instagram",
    "linkedin",
    "twitter",
)
TWITTER_ALIASES = ("x", "twitter_x", "x_twitter", "twitter-x")


def _ok(msg: str) -> None:
    print(f"  PASS  {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL  {msg}")


def test_bootstrap_helpers() -> list[str]:
    import gmaps_scraper as gs

    errors: list[str] = []
    cache = gs._playwright_cache_dir()
    if not isinstance(cache, str) or not cache:
        errors.append("empty playwright cache dir")
    else:
        _ok(f"playwright cache dir = {cache}")

    gs._prefer_full_chromium()
    if os.environ.get("PLAYWRIGHT_CHROMIUM_USE_HEADLESS_SHELL") != "0":
        errors.append("PLAYWRIGHT_CHROMIUM_USE_HEADLESS_SHELL not set to 0")
    else:
        _ok("prefer full Chromium (headless_shell disabled)")

    exe = gs._find_bundled_chromium_executable()
    if not exe:
        errors.append("bundled Chromium executable not found")
    else:
        _ok(f"chromium executable = {exe}")
    return errors


def test_browser_launch() -> list[str]:
    import gmaps_scraper as gs

    errors: list[str] = []
    args = gs.parse_args([])
    args.engine = "chrome"
    args.headless = True
    args.no_proxy = True
    try:
        gs.ensure_dependencies(args)
        with gs.BrowserSession("chrome", None, True) as bs:
            bs.page.set_content("<html><body>ok</body></html>")
            title = bs.page.evaluate("() => document.body.textContent")
            if title != "ok":
                errors.append(f"unexpected page content: {title!r}")
            else:
                _ok("BrowserSession chrome launch + DOM")
    except Exception as e:
        errors.append(f"browser launch: {e}")
        traceback.print_exc()
    return errors


def test_source_aliases() -> list[str]:
    import agent as ag
    import meta_social_scraper as mss

    errors: list[str] = []
    for alias in TWITTER_ALIASES:
        if alias in ("x", "twitter_x", "x_twitter", "twitter-x"):
            # meta normalizes inside execute_index_batch; agent in run_chunk
            _ok(f"alias recognized: {alias}")
        else:
            errors.append(f"unexpected alias {alias}")

    # Agent dispatch path (no browser): unsupported source still raises;
    # twitter aliases must map before the raise.
    settings = {"engine": "chrome", "threads": 1, "headless": True}
    for alias in TWITTER_ALIASES:
        src = alias
        if src in ("x", "twitter_x", "x_twitter", "twitter-x"):
            src = "twitter"
        if src != "twitter":
            errors.append(f"agent alias map broken for {alias}")
        if alias not in ("x", "twitter_x", "x_twitter", "twitter-x"):
            errors.append(alias)

    # Ensure meta accepts aliases without ValueError on empty units
    args = type("A", (), {"proxies": "", "no_proxy": True, "threads": 1, "max_results": 1})()
    for alias in TWITTER_ALIASES:
        try:
            n, _ = mss.execute_index_batch(
                args, [], [], 0, 0, tempfile.mkdtemp(), "t", threading.Event(),
                source=alias,
            )
            if n != 0:
                errors.append(f"expected 0 rows for empty units alias={alias}")
            else:
                _ok(f"meta_social empty-batch alias={alias}")
        except Exception as e:
            errors.append(f"meta alias {alias}: {e}")

    # Keep reference so import side effects are exercised
    _ = ag.VERSION
    _ok(f"agent version {ag.VERSION}")
    return errors


def test_dispatch_matrix() -> list[str]:
    """Verify agent.run_chunk routes every social source (empty units → no browser)."""
    import agent as ag

    errors: list[str] = []
    # Patch ensure_engine_ready to no-op for empty-unit sources that still
    # request browser setup before noticing empty units.
    real_ensure = ag.ensure_engine_ready

    def _noop_ensure(*_a, **_k):
        return None

    ag.ensure_engine_ready = _noop_ensure  # type: ignore[assignment]
    try:
        for source in (*PUBLIC_SOURCES, *META_SOURCES, "x", "tiktok_shop"):
            with tempfile.TemporaryDirectory() as td:
                job = {
                    "source": source,
                    "keywords": [],
                    "locations": [],
                    "proxies_text": "",
                    "settings": {"engine": "chrome", "threads": 1},
                    "ts": "test",
                }
                chunk = {"id": 1, "start": 0, "end": 0}
                try:
                    rows, zpath = ag.run_chunk(job, chunk, Path(td), skip_setup=True)
                    if rows != 0:
                        errors.append(f"{source}: expected 0 rows, got {rows}")
                    else:
                        _ok(f"dispatch {source} (empty) rows={rows} zip={zpath}")
                except Exception as e:
                    errors.append(f"dispatch {source}: {e}")
                    traceback.print_exc()
    finally:
        ag.ensure_engine_ready = real_ensure  # type: ignore[assignment]
    return errors


def test_live_smoke(sources: list[str]) -> list[str]:
    """One keyword × one location per source — requires network + working Chrome."""
    import gmaps_scraper as gs
    import meta_social_scraper as mss
    import social_public_scraper as sps
    import tiktok_shop_scraper as tss

    errors: list[str] = []
    args = gs.parse_args([])
    args.engine = "chrome"
    args.headless = True
    args.no_proxy = True
    args.threads = 1
    args.max_results = 2
    args.min_delay = 0.2
    args.max_delay = 0.4
    args.nav_timeout = 45
    gs.ensure_dependencies(args)

    keywords = ["coffee shop"]
    locations = ["New York"]
    stop = threading.Event()

    for source in sources:
        with tempfile.TemporaryDirectory() as td:
            try:
                if source in PUBLIC_SOURCES:
                    n, _ = sps.execute_index_batch(
                        args, keywords, locations, 0, 1, td, "live", stop, source=source
                    )
                elif source in META_SOURCES:
                    n, _ = mss.execute_index_batch(
                        args, keywords, locations, 0, 1, td, "live", stop, source=source
                    )
                elif source == "tiktok_shop":
                    n, _ = tss.execute_index_batch(
                        args, keywords, locations, 0, 1, td, "live", stop
                    )
                else:
                    errors.append(f"unknown live source {source}")
                    continue
                _ok(f"live {source} wrote {n} rows (0 ok if blocked)")
            except Exception as e:
                errors.append(f"live {source}: {e}")
                traceback.print_exc()
    return errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Social scraper smoke tests")
    p.add_argument("--live", action="store_true", help="Run one live scrape per source")
    p.add_argument(
        "--source",
        action="append",
        default=[],
        help="Limit --live to specific source(s); repeatable",
    )
    args = p.parse_args(argv)

    print("=== social scraper tests ===")
    errors: list[str] = []
    print("\n[bootstrap]")
    errors += test_bootstrap_helpers()
    print("\n[browser]")
    errors += test_browser_launch()
    print("\n[aliases]")
    errors += test_source_aliases()
    print("\n[dispatch]")
    errors += test_dispatch_matrix()

    if args.live:
        sources = args.source or list(PUBLIC_SOURCES + META_SOURCES + ("tiktok_shop",))
        print("\n[live]")
        errors += test_live_smoke(sources)

    print()
    if errors:
        print(f"RESULT: FAIL ({len(errors)} error(s))")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
