#!/usr/bin/env python3
"""
Multi-threaded Google Maps business scraper.

Reads three plain-text files:
  - proxies.txt    : one HTTP/SOCKS proxy per line
  - locations.txt  : one "city,state,country" per line
  - keywords.txt   : one search keyword per line

Every keyword is searched against every location. Each (keyword, location)
pair is a work unit distributed across a thread pool. For every business it
finds the scraper extracts: name, address, phone, email, website, review
count, category, latitude/longitude, opening hours, and any social-media
URLs, then writes that row to the CSV IMMEDIATELY (row-by-row, fsync-flushed)
so results are never lost if the run is interrupted.

Key features
------------
* Two engines: Playwright Chromium (--engine chrome) or Camoufox stealth
  Firefox (--engine camoufox). Proxies may be authenticated or not for any engine.
* Anti-bot / stealth measures (webdriver flag hidden, plugins/languages/WebGL
  spoofed, automation flags stripped, randomised user-agent, timezone/locale).
* Randomised human-like pauses everywhere + periodic long "cool-off" pauses to
  protect proxies from Google rate-limiting / bans.
* Browser cache + cookies + storage are FLUSHED every time the keyword or city
  changes (each job runs in a fresh context and is explicitly cleared).
* Runs headless on a bare Ubuntu VPS with NO desktop environment. If you would
  rather run "headed", use xvfb (see README) — a real desktop is NOT required.
* --selftest launches the chosen engine, applies stealth, clears cache and
  verifies bot-evasion on your own machine without touching Google.

Usage
-----
  pip install -r requirements.txt
  python -m playwright install chromium      # for --engine chrome
  python -m playwright install-deps          # system libs on a bare VPS
  python -m camoufox fetch                    # for --engine camoufox

  python gmaps_scraper.py --engine chrome --threads 4 --output results.csv
  python gmaps_scraper.py --selftest --engine chrome
"""

from __future__ import annotations

import argparse
import atexit
import csv
import hashlib
import hmac
import importlib.util
import itertools
import json
import os
import platform
import secrets
import signal
import socket
import zipfile
from datetime import datetime, timedelta, timezone
import re
import shutil
import subprocess
import sys
import time
import random
import threading
import traceback
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, quote_plus

# NOTE: third-party modules (requests, playwright, camoufox) are imported
# lazily *after* the dependency bootstrap so the script can install them itself
# on first run. Do not add top-level third-party imports here.

# Host OS this script is running on (macOS / Linux / Windows all supported).
HOST_OS = platform.system()  # "Darwin", "Linux", or "Windows"


# ----------------------------------------------------------------------------
# Dependency bootstrap — auto-install missing packages + browser on first run.
# Runs from main(); importing this module does NOT trigger any installs.
# ----------------------------------------------------------------------------


def _pip_install(pkgs: list[str]):
    print(f"[setup] installing Python packages: {', '.join(pkgs)}")
    cmd = [sys.executable, "-m", "pip", "install", *pkgs]
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError:
        # Fall back to a per-user install (common on system Python / macOS).
        subprocess.check_call(cmd + ["--user"])


def _default_playwright_cache_dir() -> str:
    if HOST_OS == "Windows":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
        return os.path.join(base, "ms-playwright")
    if HOST_OS == "Darwin":
        return os.path.expanduser("~/Library/Caches/ms-playwright")
    return os.path.expanduser("~/.cache/ms-playwright")


def _sanitize_playwright_browsers_path() -> None:
    """Drop a broken PLAYWRIGHT_BROWSERS_PATH override.

    Some environments (CI sandboxes, IDE runners) inject an empty cache dir via
    PLAYWRIGHT_BROWSERS_PATH. Playwright then looks there, misses Chromium, and
    social scrapers die with 'Executable doesn't exist' even though browsers
    already exist in the default user cache. If the override has no Chromium but
    the default cache does, clear the override.
    """
    override = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if not override or override == "0":
        return
    override_exp = os.path.abspath(os.path.expanduser(override))
    default = os.path.abspath(_default_playwright_cache_dir())
    if override_exp == default:
        return
    if any(_iter_chromium_executables(override_exp)):
        return
    if any(_iter_chromium_executables(default)):
        print(
            f"[setup] PLAYWRIGHT_BROWSERS_PATH={override!r} has no Chromium; "
            f"using default cache {default}",
            flush=True,
        )
        os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)


def _playwright_cache_dir() -> str:
    """Directory where Playwright stores browser builds.

    Honours ``PLAYWRIGHT_BROWSERS_PATH`` when set (same as Playwright itself),
    after sanitizing empty/broken overrides.
    """
    _sanitize_playwright_browsers_path()
    override = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if override and override != "0":
        return os.path.expanduser(override)
    return _default_playwright_cache_dir()


_CHROMIUM_BIN_NAMES = frozenset({
    "chrome",
    "chrome.exe",
    "chrome-headless-shell",
    "headless_shell",
    "Google Chrome for Testing",
})


def _prefer_full_chromium() -> None:
    """Use full Chromium for headless launches when possible.

    Playwright's ``chromium_headless_shell`` crashes (SIGSEGV) on some hosts
    (restricted sandboxes, broken GPU stubs). Prefer the full build unless the
    operator explicitly set PLAYWRIGHT_CHROMIUM_USE_HEADLESS_SHELL.
    """
    _sanitize_playwright_browsers_path()
    os.environ.setdefault("PLAYWRIGHT_CHROMIUM_USE_HEADLESS_SHELL", "0")


def _iter_chromium_executables(cache_dir: str):
    """Yield executable paths under a Playwright cache, full Chromium first."""
    try:
        names = os.listdir(cache_dir)
    except OSError:
        return
    ranked: list[tuple[int, str]] = []
    for name in names:
        if name.startswith("chromium-") and not name.startswith("chromium_headless"):
            ranked.append((0, name))
        elif name.startswith("chromium_headless_shell-") or name.startswith(
            "chromium_headless_shell"
        ):
            ranked.append((1, name))
    ranked.sort()
    for _, dirname in ranked:
        root = os.path.join(cache_dir, dirname)
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if fn not in _CHROMIUM_BIN_NAMES:
                    continue
                path = os.path.join(dirpath, fn)
                if os.path.isfile(path) and os.access(path, os.X_OK):
                    yield path


def _find_bundled_chromium_executable() -> str | None:
    # Search active cache first, then default (covers stale env overrides).
    seen: set[str] = set()
    for cache in (_playwright_cache_dir(), _default_playwright_cache_dir()):
        cache = os.path.abspath(cache)
        if cache in seen or not os.path.isdir(cache):
            continue
        seen.add(cache)
        for path in _iter_chromium_executables(cache):
            return path
    return None


def _chromium_installed() -> bool:
    return _find_bundled_chromium_executable() is not None


def _chrome_launch_error_hint(exc: BaseException) -> str:
    msg = str(exc)
    if "Executable doesn't exist" in msg or "browserType.launch" in msg.lower():
        cache = _playwright_cache_dir()
        return (
            f"\n[hint] Chromium binary missing or Playwright cache mismatch.\n"
            f"  cache dir : {cache}\n"
            f"  PLAYWRIGHT_BROWSERS_PATH={os.environ.get('PLAYWRIGHT_BROWSERS_PATH')!r}\n"
            f"  Fix: {sys.executable} -m playwright install chromium\n"
            f"  Or unset a bad PLAYWRIGHT_BROWSERS_PATH and re-run with --force-setup."
        )
    return ""


def install_brave() -> str | None:
    """Best-effort automatic install of the Brave browser for the current OS,
    using the platform's native package manager. Returns the binary path if
    Brave is available afterwards, else None."""
    existing = detect_brave_path()
    if existing:
        return existing

    print(f"[setup] Brave not found — attempting automatic install on {HOST_OS}...")
    try:
        if HOST_OS == "Darwin":
            if shutil.which("brew"):
                subprocess.check_call(["brew", "install", "--cask", "brave-browser"])
            else:
                print("[setup] Homebrew not found. Install Homebrew (https://brew.sh) "
                      "then re-run, or install Brave from https://brave.com/download/.")
        elif HOST_OS == "Linux":
            # Official Brave install script (handles Debian/Ubuntu/Fedora/etc.).
            # Needs curl; may prompt for sudo.
            if shutil.which("curl"):
                subprocess.check_call(
                    "curl -fsS https://dl.brave.com/install.sh | sh", shell=True)
            else:
                print("[setup] 'curl' not found. Install curl, or install Brave per "
                      "https://brave.com/linux/.")
        elif HOST_OS == "Windows":
            if shutil.which("winget"):
                subprocess.check_call(
                    ["winget", "install", "--id", "Brave.Brave", "-e",
                     "--silent", "--accept-package-agreements",
                     "--accept-source-agreements"])
            elif shutil.which("choco"):
                subprocess.check_call(["choco", "install", "brave", "-y"])
            else:
                print("[setup] winget/choco not found. Install Brave from "
                      "https://brave.com/download/.")
    except subprocess.CalledProcessError as e:
        print(f"[setup] automatic Brave install failed: {e}")

    return detect_brave_path()


def ensure_dependencies(args):
    """Make sure the packages + browser needed for the chosen engine exist,
    installing them automatically on first run. OS is auto-detected."""
    if getattr(args, "skip_setup", False):
        # Still sanitize browser path / prefer full Chromium so launches work
        # even when package install is skipped (agent sets skip_setup after first run).
        if args.engine in ("chrome", "google-chrome", "edge", "brave"):
            _prefer_full_chromium()
        return

    # 1) Python packages required for this engine.
    needed = {"requests": "requests"}
    if args.engine in ("chrome", "google-chrome", "edge", "brave"):
        needed["playwright"] = "playwright"
    if args.engine == "camoufox":
        needed["playwright"] = "playwright"
        needed["camoufox"] = "camoufox[geoip]"

    missing = [pip_name for mod, pip_name in needed.items()
               if importlib.util.find_spec(mod) is None]
    if missing:
        try:
            _pip_install(missing)
        except Exception as e:
            raise SystemExit(
                f"[fatal] could not auto-install {missing}: {e}\n"
                f"Install manually:  {sys.executable} -m pip install -r requirements.txt"
            )

    # psutil is optional but used for thorough child-process cleanup on shutdown.
    if importlib.util.find_spec("psutil") is None:
        try:
            _pip_install(["psutil"])
        except Exception:
            print("[setup] psutil not installed (optional) — browser cleanup on "
                  "shutdown will be best-effort without it.")

    # dnspython for email_validate MX lookups (A-record fallback if missing).
    if importlib.util.find_spec("dns") is None:
        try:
            _pip_install(["dnspython"])
        except Exception:
            print("[setup] dnspython not installed — email MX checks will use "
                  "socket A-record fallback.")

    if args.engine in ("chrome", "google-chrome", "edge", "brave"):
        _prefer_full_chromium()

    # 2) Browser binary (expensive; only done once, tracked by a per-user
    #    sentinel stored in the HOME directory — never in the script folder, so
    #    it cannot ship with the code and wrongly skip setup on someone else's
    #    machine). Always re-verify that Chromium/Brave still exists: a stale
    #    sentinel after cache wipe or PLAYWRIGHT_BROWSERS_PATH mismatch caused
    #    social scrapers to fail with 'Executable doesn't exist'.
    state_dir = os.path.join(os.path.expanduser("~"), ".gmaps_scraper")
    try:
        os.makedirs(state_dir, exist_ok=True)
    except OSError:
        state_dir = os.path.expanduser("~")
    sentinel = os.path.join(state_dir, f"setup_{args.engine}.done")

    def _invalidate_sentinel(reason: str) -> None:
        print(f"[setup] {reason} — re-running browser install…")
        try:
            os.remove(sentinel)
        except OSError:
            pass

    if not args.force_setup and os.path.exists(sentinel):
        if args.engine == "chrome" and not _chromium_installed():
            _invalidate_sentinel(
                f"setup sentinel present but Chromium missing under {_playwright_cache_dir()}"
            )
        elif args.engine == "brave" and not (args.browser_path or detect_brave_path()):
            _invalidate_sentinel("setup sentinel present but Brave binary not found")
        elif args.browser_path and not os.path.exists(args.browser_path):
            _invalidate_sentinel(f"--browser-path missing: {args.browser_path}")
        else:
            return

    # If the user supplied an explicit binary, no download is needed for any
    # Chromium-based engine.
    if args.browser_path and args.engine in ("chrome", "google-chrome", "edge", "brave"):
        if not os.path.exists(args.browser_path):
            raise SystemExit(f"[fatal] --browser-path not found: {args.browser_path}")
        _write_sentinel(sentinel)
        return

    try:
        if args.engine == "chrome":
            # Playwright's bundled Chromium — most reliable everywhere.
            if args.force_setup or not _chromium_installed():
                _pw_install("chromium")
            if not _chromium_installed():
                raise SystemExit(
                    "[fatal] Chromium install finished but no executable was found under "
                    f"{_playwright_cache_dir()}. Check PLAYWRIGHT_BROWSERS_PATH and disk space."
                )
        elif args.engine == "google-chrome":
            # Real Google Chrome (stable channel).
            _pw_install("chrome")
        elif args.engine == "edge":
            # Real Microsoft Edge (stable channel).
            _pw_install("msedge")
        elif args.engine == "camoufox":
            print("[setup] fetching Camoufox browser (first run only)...")
            subprocess.check_call([sys.executable, "-m", "camoufox", "fetch"])
        elif args.engine == "brave":
            # Brave uses its own installed binary. Auto-install it if absent.
            if not detect_brave_path():
                path = install_brave()
                if path:
                    print(f"[setup] Brave ready: {path}")
                else:
                    raise SystemExit(
                        "[fatal] Brave could not be installed automatically. "
                        "Install it from https://brave.com/download/ (or pass "
                        "--browser-path to an existing Chromium-based browser), "
                        "then re-run.")
        _write_sentinel(sentinel)
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            f"[fatal] browser setup failed for engine '{args.engine}': {e}\n"
            f"Run the matching install manually, e.g.:\n"
            f"  {sys.executable} -m playwright install chromium|chrome|msedge\n"
            f"  {sys.executable} -m camoufox fetch")


def _pw_install(target: str):
    """Install a Playwright-managed browser (chromium / chrome / msedge) and,
    on Linux, its system libraries (best-effort)."""
    labels = {"chromium": "Chromium", "chrome": "Google Chrome", "msedge": "Microsoft Edge"}
    print(f"[setup] installing {labels.get(target, target)} (first run only)...")
    subprocess.check_call([sys.executable, "-m", "playwright", "install", target])
    if HOST_OS == "Linux":
        subprocess.call([sys.executable, "-m", "playwright", "install-deps", target])


def _write_sentinel(path: str):
    try:
        with open(path, "w") as fh:
            fh.write("ok")
    except OSError:
        pass

# ----------------------------------------------------------------------------
# Global shutdown — make sure Ctrl+C / SIGTERM / terminal-close kills EVERYTHING
# (all threads, all browsers, and every child process, headless or headed).
# ----------------------------------------------------------------------------

_SHUTDOWN = threading.Event()          # set once we're tearing down
_SHUTDOWN_DONE = threading.Event()     # so cleanup only runs once
_ACTIVE_SESSIONS = set()               # live BrowserSession objects
_ACTIVE_LOCK = threading.Lock()


def _register_session(s):
    with _ACTIVE_LOCK:
        _ACTIVE_SESSIONS.add(s)


def _unregister_session(s):
    with _ACTIVE_LOCK:
        _ACTIVE_SESSIONS.discard(s)


def kill_child_processes():
    """Kill every child process of this process (browser + driver subprocesses),
    recursively. Uses psutil if available; this is what guarantees no orphaned
    Chromium/Firefox is left running in the background."""
    try:
        import psutil
    except Exception:
        return
    try:
        me = psutil.Process(os.getpid())
        kids = me.children(recursive=True)
    except Exception:
        return
    for c in kids:
        try:
            c.terminate()
        except Exception:
            pass
    try:
        _gone, alive = psutil.wait_procs(kids, timeout=3)
    except Exception:
        alive = kids
    for c in alive:
        try:
            c.kill()            # SIGKILL / TerminateProcess — no mercy
        except Exception:
            pass


def kill_active_browsers():
    """Kill all tracked browsers + every child process, WITHOUT tearing down the
    whole program. Used both by full shutdown and by the bot's /stop command."""
    # 1) hard-kill child process trees first, so any wedged browser dies fast and
    #    pending Playwright calls in worker threads return/raise immediately.
    kill_child_processes()
    # 2) tidy up tracked sessions (best-effort; procs are already gone).
    with _ACTIVE_LOCK:
        sessions = list(_ACTIVE_SESSIONS)
        _ACTIVE_SESSIONS.clear()
    for s in sessions:
        try:
            s.force_close()
        except Exception:
            pass


def shutdown_all(reason: str = ""):
    """Idempotent, best-effort teardown of all browsers + child processes AND the
    program itself (used by signal handlers / atexit)."""
    if _SHUTDOWN_DONE.is_set():
        return
    _SHUTDOWN.set()
    kill_active_browsers()
    _SHUTDOWN_DONE.set()


def _signal_handler(signum, frame):
    try:
        name = signal.Signals(signum).name
    except Exception:
        name = str(signum)
    print(f"\n[shutdown] received {name} — killing browsers and all child "
          f"processes...", flush=True)
    shutdown_all(name)
    # Unwind the main thread so the run loop stops promptly.
    raise KeyboardInterrupt


def install_shutdown_handlers():
    """Trap the ways a run can end: Ctrl+C (SIGINT), kill/terminal (SIGTERM),
    hang-up / terminal window closed (SIGHUP), Windows console break (SIGBREAK),
    plus a normal-exit backstop via atexit."""
    for signame in ("SIGINT", "SIGTERM", "SIGHUP", "SIGBREAK"):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _signal_handler)
        except (ValueError, OSError, RuntimeError):
            pass  # not available on this platform / not main thread
    atexit.register(lambda: shutdown_all("atexit"))


# ----------------------------------------------------------------------------
# Output schema
# ----------------------------------------------------------------------------

CSV_FIELDS = [
    "keyword", "query_location", "name", "address", "phone", "email", "website",
    "review_count", "category", "latitude", "longitude", "opening_hours",
    "facebook", "instagram", "twitter", "linkedin", "youtube", "tiktok",
    "pinterest", "whatsapp", "telegram", "maps_url",
]

SOCIAL_PATTERNS = {
    "facebook": re.compile(r"https?://(?:[\w-]+\.)?facebook\.com/[^\s\"'<>]+", re.I),
    "instagram": re.compile(r"https?://(?:[\w-]+\.)?instagram\.com/[^\s\"'<>]+", re.I),
    "twitter": re.compile(r"https?://(?:[\w-]+\.)?(?:twitter|x)\.com/[^\s\"'<>]+", re.I),
    "linkedin": re.compile(r"https?://(?:[\w-]+\.)?linkedin\.com/[^\s\"'<>]+", re.I),
    "youtube": re.compile(r"https?://(?:[\w-]+\.)?youtube\.com/[^\s\"'<>]+", re.I),
    "tiktok": re.compile(r"https?://(?:[\w-]+\.)?tiktok\.com/[^\s\"'<>]+", re.I),
    "pinterest": re.compile(r"https?://(?:[\w-]+\.)?pinterest\.[a-z.]+/[^\s\"'<>]+", re.I),
    "whatsapp": re.compile(r"https?://(?:wa\.me|(?:[\w-]+\.)?whatsapp\.com)/[^\s\"'<>]+", re.I),
    "telegram": re.compile(r"https?://(?:t\.me|telegram\.me)/[^\s\"'<>]+", re.I),
}

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.I)
EMAIL_BLOCKLIST_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".mp4")

# A small pool of realistic recent desktop user-agents; one is chosen per
# browser session so fingerprints vary across proxies.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

TIMEZONES = [
    "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
    "Europe/London", "Europe/Berlin", "Europe/Madrid", "Australia/Sydney",
]

# ----------------------------------------------------------------------------
# Stealth: injected into every page before any site script runs.
# Neutralises the most common headless / automation fingerprints. The
# navigator.platform value is kept consistent with the chosen user-agent so a
# Mac UA reports "MacIntel", a Windows UA reports "Win32", etc.
# ----------------------------------------------------------------------------


def ua_platform(ua: str) -> str:
    """Return the navigator.platform string that matches a user-agent."""
    u = ua.lower()
    if "windows" in u:
        return "Win32"
    if "mac os x" in u or "macintosh" in u:
        return "MacIntel"
    return "Linux x86_64"


def build_stealth_js(nav_platform: str) -> str:
    return (
        r"""
// navigator.webdriver -> undefined
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// languages
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});

// plugins (non-empty array looks human)
Object.defineProperty(navigator, 'plugins', {
  get: () => [1, 2, 3, 4, 5].map(i => ({name: 'Plugin ' + i, filename: 'p' + i}))
});

// hardware
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});

// window.chrome
window.chrome = window.chrome || {runtime: {}, app: {}, csi: function(){}, loadTimes: function(){}};

// permissions.query for notifications
const _origQuery = window.navigator.permissions && window.navigator.permissions.query;
if (_origQuery) {
  window.navigator.permissions.query = (parameters) => (
    parameters && parameters.name === 'notifications'
      ? Promise.resolve({state: Notification.permission})
      : _origQuery(parameters)
  );
}

// WebGL vendor / renderer spoof
try {
  const getParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function(p) {
    if (p === 37445) return 'Intel Inc.';                 // UNMASKED_VENDOR_WEBGL
    if (p === 37446) return 'Intel Iris OpenGL Engine';   // UNMASKED_RENDERER_WEBGL
    return getParameter.call(this, p);
  };
} catch (e) {}

// navigator.platform kept consistent with the user-agent
Object.defineProperty(navigator, 'platform', {get: () => '__PLATFORM__'});
""".replace("__PLATFORM__", nav_platform)
    )


# Kept for backwards compatibility / quick reference (Windows default).
STEALTH_JS = build_stealth_js("Win32")


# ----------------------------------------------------------------------------
# Brave / custom Chromium binary detection (per operating system)
# ----------------------------------------------------------------------------

BRAVE_PATHS = {
    "Darwin": [
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        os.path.expanduser("~/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
    ],
    "Linux": [
        "/usr/bin/brave-browser",
        "/usr/bin/brave-browser-stable",
        "/usr/bin/brave",
        "/snap/bin/brave",
        "/opt/brave.com/brave/brave-browser",
    ],
    "Windows": [
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
        os.path.expanduser(r"~\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe"),
    ],
}


def detect_brave_path() -> str | None:
    """Find the Brave executable for the current OS, or None if not found."""
    for candidate in BRAVE_PATHS.get(HOST_OS, []):
        if candidate and os.path.exists(candidate):
            return candidate
    return None

# ----------------------------------------------------------------------------
# Proxy handling
# ----------------------------------------------------------------------------


@dataclass
class Proxy:
    host: str
    port: str
    username: str | None = None
    password: str | None = None
    scheme: str = "http"

    @property
    def has_auth(self) -> bool:
        return bool(self.username) and bool(self.password)

    def as_playwright(self) -> dict:
        cfg = {"server": f"{self.scheme}://{self.host}:{self.port}"}
        if self.has_auth:
            cfg["username"] = self.username
            cfg["password"] = self.password
        return cfg

    def as_requests(self) -> dict:
        # URL-encode credentials so special characters (# % @ : etc.) in the
        # password don't corrupt the proxy URL used by the requests library.
        if self.has_auth:
            auth = f"{quote(self.username, safe='')}:{quote(self.password, safe='')}@"
        else:
            auth = ""
        url = f"{self.scheme}://{auth}{self.host}:{self.port}"
        return {"http": url, "https": url}

    def label(self) -> str:
        return f"{self.host}:{self.port}"


def _looks_like_host_port(hostpart: str) -> bool:
    """True when ``hostpart`` is a plausible ``host:port`` (not a password fragment)."""
    hostpart = (hostpart or "").strip()
    if not hostpart or ":" not in hostpart:
        return False
    if hostpart.startswith("["):
        # [IPv6]:port
        if "]" not in hostpart:
            return False
        _, _, rest = hostpart.partition("]")
        if not rest.startswith(":"):
            return False
        port = rest[1:]
        return port.isdigit() and 1 <= int(port) <= 65535
    host, port = hostpart.rsplit(":", 1)
    if not host or not port.isdigit():
        return False
    try:
        return 1 <= int(port) <= 65535
    except ValueError:
        return False


def _parse_host_port_user_pass(line: str) -> tuple[str, str, str | None, str | None] | None:
    """Parse ``host:port`` or ``host:port:user:password`` (password may contain ``:`` / ``@``).

    Splits on the first three colons only so credentials stay intact.
    """
    parts = line.split(":", 3)
    if len(parts) == 2:
        return parts[0], parts[1], None, None
    if len(parts) >= 4:
        return parts[0], parts[1], parts[2], parts[3]
    if len(parts) == 3:
        # Ambiguous third field — treat as host:port only (legacy).
        return parts[0], parts[1], None, None
    return None


def parse_proxy(line: str) -> Proxy | None:
    """Parse a proxy line in any supported format (see README).

    Supported:
      - ``host:port``
      - ``host:port:user:password`` (password may contain ``@``, ``:``, etc.)
      - ``user:password@host:port`` (password may contain ``@`` — use last ``@``)
      - optional ``scheme://`` prefix (http, https, socks5)
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    scheme = "http"
    if "://" in line:
        scheme, line = line.split("://", 1)
        scheme = scheme.lower().strip()

    username = password = None
    host = port = ""

    # ``user:pass@host:port`` only when the RHS of the *last* @ looks like host:port.
    # Otherwise ``@`` is inside a ``host:port:user:password`` password — do not rsplit.
    if "@" in line:
        left, right = line.rsplit("@", 1)
        if _looks_like_host_port(right) and ":" in left:
            username, password = left.split(":", 1)
            host, port = _split_host_port(right)
        else:
            parsed = _parse_host_port_user_pass(line)
            if parsed is None:
                return None
            host, port, username, password = parsed
    else:
        parsed = _parse_host_port_user_pass(line)
        if parsed is None:
            return None
        host, port, username, password = parsed

    if not host or not port:
        return None
    return Proxy(host=host.strip(), port=port.strip(),
                 username=username, password=password, scheme=scheme)


def _split_host_port(hostpart: str) -> tuple[str, str]:
    if ":" in hostpart:
        host, port = hostpart.rsplit(":", 1)
        return host, port
    return hostpart, ""


def load_proxies(path: str, require_auth: bool) -> list[Proxy]:
    proxies: list[Proxy] = []
    if not os.path.exists(path):
        raise SystemExit(f"[fatal] proxy file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            p = parse_proxy(raw)
            if p is None:
                if raw.strip() and not raw.strip().startswith("#"):
                    print(f"[proxy] skipped unparseable line {lineno}: {raw.strip()}")
                continue
            if require_auth and not p.has_auth:
                raise SystemExit(
                    f"[fatal] --engine camoufox requires proxies with username:password, "
                    f"but line {lineno} has none: {raw.strip()}"
                )
            proxies.append(p)
    if not proxies:
        raise SystemExit(f"[fatal] no usable proxies found in {path}")
    return proxies


# A lightweight Google endpoint that returns HTTP 204 quickly — perfect for
# checking whether a proxy can actually reach Google at all.
PROXY_TEST_URL = "https://www.google.com/generate_204"


def preflight_proxy(proxy: "Proxy | None", timeout: float = 12.0) -> tuple[bool, str]:
    """Quick check that a proxy can reach Google. Returns (ok, detail)."""
    if proxy is None:
        return True, "no-proxy (direct)"
    import requests
    try:
        r = requests.get(PROXY_TEST_URL, proxies=proxy.as_requests(),
                         timeout=timeout,
                         headers={"User-Agent": random.choice(USER_AGENTS)})
        if r.status_code in (204, 200):
            return True, f"reachable (HTTP {r.status_code})"
        return False, f"unexpected HTTP {r.status_code}"
    except requests.exceptions.ProxyError as e:
        return False, f"proxy error ({_short_err(e)})"
    except requests.exceptions.ConnectTimeout:
        return False, "connect timeout"
    except requests.exceptions.ReadTimeout:
        return False, "read timeout (proxy/Google too slow)"
    except requests.exceptions.SSLError as e:
        return False, f"ssl error ({_short_err(e)})"
    except Exception as e:
        return False, _short_err(e)


def _short_err(e) -> str:
    s = str(e)
    return (s[:90] + "…") if len(s) > 90 else s


# ----------------------------------------------------------------------------
# Input loading
# ----------------------------------------------------------------------------


def load_lines(path: str) -> list[str]:
    if not os.path.exists(path):
        raise SystemExit(f"[fatal] file not found: {path}")
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            s = raw.strip()
            if s and not s.startswith("#"):
                out.append(s)
    return out


def format_location(line: str) -> str:
    parts = [p.strip() for p in line.split(",") if p.strip()]
    return ", ".join(parts)


# ----------------------------------------------------------------------------
# Thread-safe, instant CSV writer
# ----------------------------------------------------------------------------


class CsvWriter:
    """Writes each row the instant it is produced, fsync-flushed, with a lock so
    multiple threads share one file safely. Dedupes by (name, address)."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._seen: set[tuple[str, str]] = set()
        file_exists = os.path.exists(path) and os.path.getsize(path) > 0
        # On resume/append, preload existing (name,address) so we don't rewrite
        # rows already saved in a previous session.
        if file_exists:
            try:
                with open(path, "r", newline="", encoding="utf-8") as fh:
                    for r in csv.DictReader(fh):
                        self._seen.add((r.get("name", "").strip().lower(),
                                        r.get("address", "").strip().lower()))
            except Exception:
                pass
        self._fh = open(path, "a", newline="", encoding="utf-8", buffering=1)
        self._writer = csv.DictWriter(self._fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if not file_exists:
            self._writer.writeheader()
            self._fh.flush()

    def write(self, row: dict) -> bool:
        key = (row.get("name", "").strip().lower(), row.get("address", "").strip().lower())
        with self._lock:
            if key in self._seen:
                return False
            self._seen.add(key)
            self._writer.writerow(row)
            self._fh.flush()
            try:
                os.fsync(self._fh.fileno())
            except OSError:
                pass
        return True

    def close(self):
        with self._lock:
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:
                pass


def safe_filename(text: str) -> str:
    """Turn 'Austin, Texas, USA' into a filesystem-safe 'Austin_Texas_USA'."""
    s = re.sub(r"[^\w\-]+", "_", text).strip("_")
    return s or "location"


class PerLocationWriter:
    """Routes every row to a separate CSV per location (city/state/country).
    Each file is named '<City_State_Country>_<YYYY-MM-DD_HH-MM-SS>.csv', is
    created the first time that location produces a result, written to instantly
    (fsync-flushed), and de-duplicated within that location. All keywords for a
    location land in the same file."""

    def __init__(self, out_dir: str, timestamp: str):
        self.out_dir = out_dir
        self.timestamp = timestamp
        os.makedirs(out_dir, exist_ok=True)
        self._writers: dict[str, CsvWriter] = {}
        self._lock = threading.Lock()

    def _writer_for(self, location: str) -> CsvWriter:
        with self._lock:
            w = self._writers.get(location)
            if w is None:
                fname = f"{safe_filename(location)}_{self.timestamp}.csv"
                path = os.path.join(self.out_dir, fname)
                w = CsvWriter(path)
                self._writers[location] = w
            return w

    def write(self, location: str, row: dict) -> bool:
        return self._writer_for(location).write(row)

    def files(self) -> dict:
        return {loc: w.path for loc, w in self._writers.items()}

    def close(self):
        for w in list(self._writers.values()):
            w.close()


# ----------------------------------------------------------------------------
# Session checkpoint / resume
# ----------------------------------------------------------------------------


class SessionState:
    """Tracks how far a run got so an interrupted run resumes where it stopped.

    Uses a single integer CURSOR (number of jobs processed, in the deterministic
    keyword×location order) rather than a set of every completed job — so it
    stays tiny even for hundreds of millions of jobs. The cursor is advanced and
    persisted after each chunk finishes."""

    FILENAME = ".gmaps_session.json"

    def __init__(self, path: str, timestamp: str, signature: str, total: int):
        self.path = path
        self.timestamp = timestamp       # reused for CSV filenames so we append
        self.signature = signature       # fingerprint of inputs (engine+kw+loc)
        self.total = total
        self.cursor = 0                  # jobs processed so far (in order)
        self._lock = threading.Lock()

    @staticmethod
    def signature_for(engine: str, keywords: list[str], locations: list[str]) -> str:
        blob = "§".join(["|".join(sorted(keywords)), "|".join(sorted(locations)), engine])
        return hashlib.md5(blob.encode("utf-8")).hexdigest()

    @classmethod
    def load_or_new(cls, path, signature, timestamp, total, resume, log):
        """Return (session, resumed_bool)."""
        if resume and os.path.exists(path):
            try:
                data = json.load(open(path, "r", encoding="utf-8"))
                if data.get("signature") == signature:
                    s = cls(path, data.get("created", timestamp), signature, total)
                    s.cursor = int(data.get("cursor", 0))
                    return s, True
                log("[session] saved session is for different keywords/locations/"
                    "engine — starting a fresh run.")
            except Exception as e:
                log(f"[session] could not read saved session ({_short_err(e)}) — "
                    f"starting fresh.")
        s = cls(path, timestamp, signature, total)
        s._persist()
        return s, False

    def set_cursor(self, n: int):
        with self._lock:
            self.cursor = n
            self._persist()

    def _persist(self):
        data = {
            "version": 2,
            "created": self.timestamp,
            "signature": self.signature,
            "total_jobs": self.total,
            "cursor": self.cursor,
        }
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.path)   # atomic
        except OSError:
            pass

    def clear(self):
        try:
            os.remove(self.path)
        except OSError:
            pass


class FailedLog:
    """Thread-safe append log of jobs that didn't complete (so nothing is lost
    when the cursor moves past them). One row: keyword<TAB>location<TAB>reason."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._fh = None
        self.count = 0

    def add(self, keyword: str, location: str, reason: str):
        with self._lock:
            try:
                if self._fh is None:
                    self._fh = open(self.path, "a", encoding="utf-8", buffering=1)
                self._fh.write(f"{keyword}\t{location}\t{reason}\n")
                self.count += 1
            except OSError:
                pass

    def close(self):
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:
                    pass


# ----------------------------------------------------------------------------
# Website enrichment: email + social links
# ----------------------------------------------------------------------------


def enrich_from_website(website: str, proxy: Proxy | None, ua: str) -> dict:
    import requests  # lazy: installed by the bootstrap on first run
    result = {k: "" for k in ["email"] + list(SOCIAL_PATTERNS.keys())}
    if not website:
        return result

    session = requests.Session()
    session.headers.update({"User-Agent": ua})
    if proxy is not None:
        session.proxies.update(proxy.as_requests())

    base = website.rstrip("/")
    pages_to_try = [website, base + "/contact", base + "/contact-us",
                    base + "/about", base + "/about-us"]

    html_blobs = []
    for url in pages_to_try:
        try:
            resp = session.get(url, timeout=15, allow_redirects=True)
            if resp.status_code == 200 and resp.text:
                html_blobs.append(resp.text)
        except Exception:
            continue
        if url == website and _first_email(" ".join(html_blobs)):
            pages_to_try = pages_to_try[:2]

    combined = "\n".join(html_blobs)
    email = _first_email(combined)
    if email:
        result["email"] = email
    for name, pat in SOCIAL_PATTERNS.items():
        m = pat.search(combined)
        if m:
            result[name] = _clean_url(m.group(0))
    return result


def _first_email(text: str) -> str:
    mailtos = re.findall(r"mailto:([^\s\"'<>?]+)", text, re.I)
    for c in mailtos + EMAIL_PATTERN.findall(text):
        c = c.strip().strip(".")
        low = c.lower()
        if any(low.endswith(ext) for ext in EMAIL_BLOCKLIST_EXT):
            continue
        if low.startswith(("example@", "email@", "your@", "name@", "user@")):
            continue
        if any(bad in low for bad in ("sentry", "wixpress", "@2x", "@3x", ".png", ".jpg")):
            continue
        return c
    return ""


def _clean_url(url: str) -> str:
    return url.rstrip("\".',)>").strip()


# ----------------------------------------------------------------------------
# Human-like pacing (protects proxies from bans)
# ----------------------------------------------------------------------------


class Pacer:
    """Central place for all randomised delays + a global cool-off counter."""

    def __init__(self, min_delay: float, max_delay: float,
                 cooldown_every: int, cooldown_min: float, cooldown_max: float):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.cooldown_every = cooldown_every
        self.cooldown_min = cooldown_min
        self.cooldown_max = cooldown_max
        self._req_count = 0
        self._lock = threading.Lock()

    def short(self):
        """Small pause between minor actions."""
        time.sleep(random.uniform(self.min_delay * 0.4, self.max_delay * 0.6))

    def between_listings(self):
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def between_jobs(self):
        time.sleep(random.uniform(self.max_delay, self.max_delay * 2.2))

    def tick(self, log):
        """Count a request; occasionally force a long cool-off to dodge bans."""
        do_cooldown = False
        with self._lock:
            self._req_count += 1
            if self.cooldown_every > 0 and self._req_count % self.cooldown_every == 0:
                do_cooldown = True
        if do_cooldown:
            nap = random.uniform(self.cooldown_min, self.cooldown_max)
            log(f"cool-off pause {nap:.0f}s (proxy protection)")
            time.sleep(nap)

    def ms(self, lo: int, hi: int) -> int:
        return random.randint(lo, hi)


# ----------------------------------------------------------------------------
# Browser drivers with stealth + cache flushing
# ----------------------------------------------------------------------------


class BrowserSession:
    """Engine-agnostic browser + context + page for a single work unit."""

    def __init__(self, engine: str, proxy: Proxy | None, headless: bool,
                 stealth: bool = True, ua: str | None = None,
                 timezone: str | None = None, browser_path: str | None = None,
                 geoip: bool = False, block_resources: str = "media"):
        self.engine = engine
        self.proxy = proxy
        self.headless = headless
        self.stealth = stealth
        self.ua = ua or random.choice(USER_AGENTS)
        self.timezone = timezone or random.choice(TIMEZONES)
        self.browser_path = browser_path
        self.geoip = geoip
        self.block_resources = block_resources
        self.exec_path = None            # resolved executable actually launched
        self._pw = None
        self._camoufox = None
        self.browser = None
        self.context = None
        self.page = None
        self._cdp = None

    # -- lifecycle ----------------------------------------------------------

    def __enter__(self):
        try:
            if self.engine == "camoufox":
                self._start_camoufox()
            else:
                self._start_chrome()
        except Exception:
            # If launch fails partway, tear down cleanly so a leaked Playwright
            # instance doesn't poison the next job on this thread ("Sync API
            # inside the asyncio loop").
            self._cleanup(None, None, None)
            raise
        _register_session(self)   # so global shutdown can close us
        return self

    def force_close(self):
        """Called by the global shutdown path to tear this session down now."""
        self._cleanup(None, None, None)

    def _chrome_args(self) -> list[str]:
        # Args that make Chromium look less automated, cross-platform.
        args = [
            "--disable-gpu",
            # QUIC/HTTP3 (UDP) can't traverse an HTTP CONNECT proxy and makes
            # Chromium hang on Google domains behind a proxy — force TCP.
            "--disable-quic",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process,AutomationControlled",
            "--disable-infobars",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1366,900",
            "--lang=en-US",
            # --- memory / CPU savers (important when running several threads) ---
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-component-update",
            "--disable-domain-reliability",
            "--disable-sync",
            "--mute-audio",
            "--no-zygote",
            "--js-flags=--max-old-space-size=512",
        ]
        # Sandbox / shared-memory flags are needed on Linux (esp. root VPS /
        # containers); harmless but unnecessary on macOS/Windows so we skip them.
        if HOST_OS == "Linux":
            args = ["--no-sandbox", "--disable-dev-shm-usage"] + args
        return args

    # Playwright channel for branded Chromium builds (real Chrome / Edge).
    _CHANNELS = {"google-chrome": "chrome", "edge": "msedge"}

    def _resolve_executable(self) -> str | None:
        """Pick an explicit browser binary: user override > Brave auto-detect.
        Returns None for bundled Chromium or channel-based engines (Chrome/Edge),
        which Playwright locates itself."""
        if self.browser_path:
            if not os.path.exists(self.browser_path):
                raise SystemExit(f"[fatal] --browser-path not found: {self.browser_path}")
            return self.browser_path
        if self.engine == "brave":
            path = detect_brave_path()
            if not path:
                raise SystemExit(
                    "[fatal] Brave not found. Install Brave, or pass its binary "
                    "with --browser-path. Looked in: "
                    + ", ".join(BRAVE_PATHS.get(HOST_OS, []))
                )
            return path
        return None  # bundled Chromium, or channel handles google-chrome/edge

    def _start_chrome(self):
        from playwright.sync_api import sync_playwright

        _prefer_full_chromium()
        self._pw = sync_playwright().start()
        launch_kwargs = {"headless": self.headless, "args": self._chrome_args()}
        if self.proxy is not None:
            launch_kwargs["proxy"] = self.proxy.as_playwright()
        # exclude the automation switch that flags navigator.webdriver
        launch_kwargs["ignore_default_args"] = ["--enable-automation"]
        # Explicit binary (Brave / --browser-path) takes priority; otherwise a
        # channel selects real Chrome/Edge; otherwise bundled Chromium.
        self.exec_path = self._resolve_executable()
        if self.exec_path:
            launch_kwargs["executable_path"] = self.exec_path
        elif self.engine in self._CHANNELS:
            launch_kwargs["channel"] = self._CHANNELS[self.engine]
        try:
            self.browser = self._pw.chromium.launch(**launch_kwargs)
        except Exception as first_err:
            # Recover from missing/corrupt Playwright browser once.
            recoverable = (
                self.engine == "chrome"
                and not self.exec_path
                and (
                    "Executable doesn't exist" in str(first_err)
                    or "chromium_headless_shell" in str(first_err)
                )
            )
            if not recoverable:
                hint = _chrome_launch_error_hint(first_err)
                raise RuntimeError(
                    f"Browser launch failed (engine={self.engine}): {first_err}{hint}"
                ) from first_err
            print(
                "[browser] Chromium launch failed — reinstalling Playwright Chromium once…",
                flush=True,
            )
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None
            try:
                _pw_install("chromium")
            except Exception as install_err:
                raise RuntimeError(
                    f"Browser launch failed and Chromium reinstall failed: {first_err}\n"
                    f"reinstall error: {install_err}"
                    f"{_chrome_launch_error_hint(first_err)}"
                ) from install_err
            _prefer_full_chromium()
            self._pw = sync_playwright().start()
            try:
                self.browser = self._pw.chromium.launch(**launch_kwargs)
            except Exception as second_err:
                raise RuntimeError(
                    f"Browser launch failed after Chromium reinstall: {second_err}"
                    f"{_chrome_launch_error_hint(second_err)}"
                ) from second_err
        self.context = self.browser.new_context(
            user_agent=self.ua,
            locale="en-US",
            timezone_id=self.timezone,
            viewport={"width": 1366, "height": 900},
            java_script_enabled=True,
        )
        if self.stealth:
            self.context.add_init_script(build_stealth_js(ua_platform(self.ua)))
        self._apply_resource_blocking()
        self.page = self.context.new_page()
        try:
            self._cdp = self.context.new_cdp_session(self.page)
        except Exception:
            self._cdp = None

    # -- resource blocking (saves memory / CPU / bandwidth) ----------------

    _BLOCK_SETS = {
        "none": set(),
        "images": {"image"},
        "media": {"image", "media", "font"},
        "all": {"image", "media", "font", "stylesheet"},
    }

    def _apply_resource_blocking(self):
        """Abort heavy sub-resources (images/media/fonts/…) so each browser uses
        far less RAM/CPU. Business data is text/DOM, so this is safe."""
        types = self._BLOCK_SETS.get(self.block_resources, self._BLOCK_SETS["media"])
        if not types:
            return

        def _route(route):
            try:
                if route.request.resource_type in types:
                    return route.abort()
                return route.continue_()
            except Exception:
                try:
                    return route.continue_()
                except Exception:
                    return None

        try:
            self.context.route("**/*", _route)
        except Exception:
            pass

    def _start_camoufox(self):
        # Ensure this worker thread has a clean (non-running) asyncio loop, so
        # Camoufox's sync API doesn't trip "Sync API inside the asyncio loop".
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running() or loop.is_closed():
                raise RuntimeError
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

        from camoufox.sync_api import Camoufox

        proxy_cfg = self.proxy.as_playwright() if self.proxy is not None else None
        # geoip is OFF by default: it does an async exit-IP lookup that fails on
        # many proxies ("Failed to get IP address") and can poison the thread's
        # event loop. Enable only with --geoip when your proxies support it.
        self._camoufox = Camoufox(
            headless=self.headless,
            proxy=proxy_cfg,
            humanize=True,                       # human-like cursor movement
            geoip=bool(self.geoip and self.proxy is not None),
            locale="en-US",
            os=["windows", "macos", "linux"],    # rotate fingerprint OS
        )
        self.browser = self._camoufox.__enter__()
        self.context = self.browser.new_context()
        if self.stealth:
            try:
                self.context.add_init_script(build_stealth_js(ua_platform(self.ua)))
            except Exception:
                pass
        self._apply_resource_blocking()
        self.page = self.context.new_page()

    # -- cache / cookie flushing -------------------------------------------

    def clear_browser_data(self):
        """Flush cache, cookies and storage. Called whenever the search
        keyword or city changes so no state leaks between queries."""
        # cookies (works on both engines)
        try:
            self.context.clear_cookies()
        except Exception:
            pass
        # cache via CDP (Chromium only)
        if self._cdp is not None:
            for cmd in ("Network.clearBrowserCache", "Network.clearBrowserCookies"):
                try:
                    self._cdp.send(cmd)
                except Exception:
                    pass
        # local / session storage
        try:
            self.page.goto("about:blank")
            self.page.evaluate(
                "() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e){} }"
            )
        except Exception:
            pass

    def _cleanup(self, exc_type, exc, tb):
        _unregister_session(self)
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        try:
            if self.engine == "camoufox" and self._camoufox is not None:
                self._camoufox.__exit__(exc_type, exc, tb)
            else:
                if self.browser:
                    self.browser.close()
                if self._pw:
                    self._pw.stop()
        except Exception:
            pass
        # reset so a reused instance/thread starts clean
        self.context = self.browser = self._pw = self._camoufox = self._cdp = None

    def __exit__(self, exc_type, exc, tb):
        self._cleanup(exc_type, exc, tb)
        return False


# ----------------------------------------------------------------------------
# Google Maps scraping logic
# ----------------------------------------------------------------------------


def build_search_url(keyword: str, location: str) -> str:
    query = f"{keyword} in {location}".strip()
    return f"https://www.google.com/maps/search/{quote_plus(query)}?hl=en"


def robust_goto(page, url: str, timeout_s: int, log, retries: int = 1) -> bool:
    """Navigate resiliently. Uses wait_until='commit' (fires as soon as the
    server responds) so a slow proxy fails fast instead of hanging the whole
    timeout, then waits briefly for the app shell. Retries once. Returns True
    on success."""
    from playwright.sync_api import TimeoutError as PWTimeout
    last = ""
    for attempt in range(retries + 1):
        try:
            page.goto(url, timeout=timeout_s * 1000, wait_until="commit")
            try:
                page.wait_for_load_state("domcontentloaded",
                                         timeout=min(timeout_s, 20) * 1000)
            except Exception:
                pass
            return True
        except PWTimeout:
            last = f"timed out after {timeout_s}s"
        except Exception as e:
            last = _short_err(e)
        if attempt < retries:
            log(f"navigation failed ({last}); retry {attempt + 1}")
    log(f"navigation failed ({last}) — proxy likely can't load this page in-browser")
    return False


def handle_consent(page):
    for sel in (
        'button[aria-label="Accept all"]',
        'button[aria-label="Reject all"]',
        'form[action*="consent"] button',
        'button:has-text("Accept all")',
        'button:has-text("I agree")',
    ):
        try:
            btn = page.locator(sel).first
            if btn.count() > 0:
                btn.click(timeout=3000)
                page.wait_for_timeout(1000)
                return
        except Exception:
            continue


END_MARKERS = (
    "You've reached the end of the list",
    "reached the end of the list",
)

# JS that collects every place link currently rendered in the results feed.
_COLLECT_JS = ("() => Array.from(document.querySelectorAll("
               "'div[role=\"feed\"] a[href*=\"/maps/place/\"]')).map(e => e.href)")


def scroll_results(page, max_results: int, pacer: Pacer, log) -> list[str]:
    """Scroll the Google Maps results feed until EVERY business is loaded.

    Google lazy-loads the feed in pages; we keep scrolling the feed container to
    the bottom and re-collecting links until either the real end-of-list marker
    appears or the count stops growing for many consecutive tries (slow proxy
    tolerance). max_results <= 0 means unlimited (load them all)."""
    from playwright.sync_api import TimeoutError as PWTimeout

    unlimited = (max_results is None or max_results <= 0)

    try:
        page.wait_for_selector('div[role="feed"]', timeout=20000)
    except PWTimeout:
        # A single strong match can open straight into the detail panel.
        if "/maps/place/" in page.url:
            return [page.url]
        log("no results feed appeared")
        return []

    seen: list[str] = []
    seen_set: set[str] = set()
    stagnant = 0
    last_count = 0
    # Be patient: only give up after many no-growth passes, so nothing is
    # skipped when the proxy/network is slow to lazy-load the next page.
    max_stagnant = 12

    def collect():
        try:
            for href in page.evaluate(_COLLECT_JS):
                if href not in seen_set:
                    seen_set.add(href)
                    seen.append(href)
        except Exception:
            pass

    while stagnant < max_stagnant and (unlimited or len(seen) < max_results):
        collect()

        content = page.content() or ""
        reached_end = any(mk in content for mk in END_MARKERS)

        # Scroll the feed to the very bottom (and nudge the last card into view
        # to trigger the next lazy-load batch).
        try:
            page.eval_on_selector('div[role="feed"]',
                                  "el => el.scrollTo(0, el.scrollHeight)")
            page.eval_on_selector_all(
                'div[role="feed"] a[href*="/maps/place/"]',
                "els => { if (els.length) els[els.length-1].scrollIntoView(); }")
        except Exception:
            try:
                page.eval_on_selector('div[role="feed"]', "el => el.scrollBy(0, 3000)")
            except Exception:
                break

        page.wait_for_timeout(pacer.ms(1600, 2800))  # let the next batch load
        collect()

        if reached_end:
            collect()
            log(f"reached end of list — {len(seen)} businesses loaded")
            break

        if len(seen) == last_count:
            stagnant += 1
        else:
            stagnant = 0
        last_count = len(seen)

    else:
        if not unlimited and len(seen) >= max_results:
            log(f"hit max-results cap ({max_results})")
        elif stagnant >= max_stagnant:
            log(f"feed stopped growing — {len(seen)} businesses loaded")

    return seen if unlimited else seen[:max_results]


def _text_or_empty(page, selector: str) -> str:
    try:
        loc = page.locator(selector).first
        if loc.count() > 0:
            return (loc.inner_text(timeout=3000) or "").strip()
    except Exception:
        pass
    return ""


def _attr_or_empty(page, selector: str, attr: str) -> str:
    try:
        loc = page.locator(selector).first
        if loc.count() > 0:
            return (loc.get_attribute(attr, timeout=3000) or "").strip()
    except Exception:
        pass
    return ""


def _strip_icon_label(value: str, prefix: str) -> str:
    v = value.strip()
    if v.lower().startswith(prefix.lower()):
        v = v[len(prefix):].strip()
    return v


def _digits_only(value: str) -> str:
    return re.sub(r"[^\d]", "", value or "")


def _parse_review_count(text: str) -> str:
    """Pull an integer review count from aria-label / visible text."""
    if not text:
        return ""
    m = re.search(r"([\d][\d,\.\s]*)\s+reviews?\b", text, re.I)
    if m:
        return _digits_only(m.group(1))
    m = re.search(r"\(([\d][\d,\.\s]*)\)", text)
    if m:
        return _digits_only(m.group(1))
    return ""


def _parse_coords_from_url(url: str) -> tuple[str, str]:
    """Prefer place-pin !3d/!4d; fall back to viewport @lat,lng."""
    if not url:
        return "", ""
    m = re.search(r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)", url)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", url)
    if m:
        return m.group(1), m.group(2)
    return "", ""


# Buttons in the place header that are never the business category.
_CATEGORY_SKIP = frozenset({
    "directions", "save", "nearby", "send to phone", "share", "call", "website",
    "reserve", "order online", "menu", "suggest an edit", "add a photo",
    "write a review", "reviews", "about", "overview", "photos", "updates",
})


def _extract_review_count(page) -> str:
    """Review total from the rating row / review-chart control."""
    for sel in (
        'div.F7nice span[aria-label*="review" i]',
        'span[aria-label*="review" i]',
        'button[aria-label*="review" i]',
        'button[jsaction*="pane.reviewChart"]',
        'button[jsaction*="reviewChart"]',
        'a[href*="/reviews"]',
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            for raw in (
                loc.get_attribute("aria-label", timeout=2000) or "",
                loc.inner_text(timeout=2000) or "",
            ):
                n = _parse_review_count(raw)
                if n:
                    return n
        except Exception:
            continue
    # Fallback: rating widget text often looks like "4.5(1,234)".
    try:
        blob = _text_or_empty(page, "div.F7nice")
        n = _parse_review_count(blob)
        if n:
            return n
    except Exception:
        pass
    return ""


def _extract_category(page) -> str:
    """Primary Google business category from the detail header."""
    for sel in (
        'button[jsaction*="pane.rating.category"]',
        'button[jsaction*="category"]',
        "button.DkEaL",
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            text = (loc.inner_text(timeout=2000) or "").strip()
            if text and text.lower() not in _CATEGORY_SKIP:
                # Category chips are short; skip long action labels.
                if len(text) <= 80 and "\n" not in text:
                    return text
        except Exception:
            continue
    return ""


def _normalize_hours_text(text: str) -> str:
    """Collapse whitespace; join multi-line day rows with ' | '."""
    if not text:
        return ""
    lines = []
    for raw in text.replace("\r", "\n").split("\n"):
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            lines.append(line)
    if not lines:
        return ""
    # Prefer day-row join when the panel expanded into a weekly table.
    day_re = re.compile(
        r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Monday|Tuesday|Wednesday|"
        r"Thursday|Friday|Saturday|Sunday)\b",
        re.I,
    )
    day_lines = [ln for ln in lines if day_re.match(ln)]
    if len(day_lines) >= 2:
        return " | ".join(day_lines)
    return " | ".join(lines) if len(lines) > 1 else lines[0]


def _extract_opening_hours(page, pacer: Pacer | None = None) -> str:
    """Weekly hours from the place-panel hours control / table."""
    # 1) Collapsed summary on the hours button (often "Open ⋅ Closes 6 PM").
    summary = ""
    for sel in (
        'button[data-item-id="oh"]',
        '[data-item-id="oh"]',
        'button[aria-label*="Hours" i]',
        'button[aria-label*="Open" i]',
        'button[aria-label*="Closed" i]',
        'div[aria-label*="Hours" i]',
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            for raw in (
                loc.get_attribute("aria-label", timeout=2000) or "",
                loc.inner_text(timeout=2000) or "",
            ):
                cleaned = _normalize_hours_text(
                    _strip_icon_label(_strip_icon_label(raw, "Hours:"), "Hours"))
                if cleaned and len(cleaned) >= 4:
                    summary = cleaned
                    break
            if summary:
                break
        except Exception:
            continue

    # 2) Expand the hours widget to capture the full Mon–Sun table when present.
    expanded = ""
    try:
        btn = page.locator('button[data-item-id="oh"]').first
        if btn.count() == 0:
            btn = page.locator('[data-item-id="oh"]').first
        if btn.count() > 0:
            try:
                btn.click(timeout=2500)
                wait_ms = pacer.ms(400, 900) if pacer else 600
                page.wait_for_timeout(wait_ms)
            except Exception:
                pass
    except Exception:
        pass

    for sel in (
        'table[aria-label*="Hours" i] tr',
        'div[role="tooltip"] table tr',
        'div.t39EBf table tr',
        '[data-hide-tooltip-on-mouse-move] table tr',
    ):
        try:
            rows = page.locator(sel)
            n = min(rows.count(), 14)
            if n == 0:
                continue
            parts = []
            for i in range(n):
                try:
                    t = (rows.nth(i).inner_text(timeout=1500) or "").strip()
                except Exception:
                    continue
                t = _normalize_hours_text(t.replace("\t", " "))
                if t:
                    parts.append(t)
            if len(parts) >= 2:
                expanded = " | ".join(parts)
                break
        except Exception:
            continue

    if not expanded:
        # Some layouts render day rows without a <table>.
        try:
            blob = page.evaluate(
                r"""() => {
                  const days = /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b/i;
                  const nodes = Array.from(document.querySelectorAll(
                    'table tr, div[role="tooltip"] div, [data-item-id="oh"] ~ * div, .t39EBf div'));
                  const lines = [];
                  for (const el of nodes) {
                    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
                    if (t && days.test(t) && t.length < 80) lines.push(t);
                  }
                  return [...new Set(lines)].slice(0, 7).join(' | ');
                }"""
            ) or ""
            blob = str(blob).strip()
            if blob.count("|") >= 1 or re.search(
                    r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Monday)\b", blob, re.I):
                expanded = blob
        except Exception:
            pass

    return expanded or summary


def _extract_place_meta_js(page) -> dict:
    """Best-effort JSON-LD / APP state fill-ins for reviews, category, coords, hours."""
    try:
        return page.evaluate(
            r"""() => {
              const out = {
                review_count: '', category: '', latitude: '', longitude: '',
                opening_hours: ''
              };
              const dig = (s) => (s || '').replace(/[^\d]/g, '');
              const dayName = (d) => String(d || '').split('/').pop();

              const fmtSpec = (specs) => {
                if (!specs) return '';
                const arr = Array.isArray(specs) ? specs : [specs];
                const parts = [];
                for (const spec of arr) {
                  if (!spec || typeof spec !== 'object') continue;
                  let days = spec.dayOfWeek || spec.daysOfWeek || '';
                  if (Array.isArray(days)) days = days.map(dayName).join(',');
                  else days = dayName(days);
                  const opens = spec.opens || '';
                  const closes = spec.closes || '';
                  if (days && opens && closes) parts.push(days + ' ' + opens + '-' + closes);
                  else if (days && (opens || closes)) parts.push((days + ' ' + (opens || closes)).trim());
                }
                return parts.join(' | ');
              };

              const apply = (obj) => {
                if (!obj || typeof obj !== 'object') return;
                if (Array.isArray(obj)) { obj.forEach(apply); return; }
                const ar = obj.aggregateRating || obj.AggregateRating;
                if (ar && !out.review_count) {
                  const rc = ar.reviewCount || ar.ratingCount;
                  if (rc != null) out.review_count = dig(String(rc));
                }
                if (!out.category) {
                  const cat = obj.category || obj.genre || obj['@type'];
                  if (typeof cat === 'string' && cat && cat !== 'LocalBusiness'
                      && cat !== 'Organization' && cat !== 'Place') {
                    out.category = cat.replace(/([a-z])([A-Z])/g, '$1 $2');
                  } else if (Array.isArray(cat) && cat.length) {
                    const c = cat.find(x => typeof x === 'string' && x !== 'LocalBusiness') || '';
                    if (c) out.category = String(c).replace(/([a-z])([A-Z])/g, '$1 $2');
                  }
                }
                const geo = obj.geo || obj.GeoCoordinates;
                if (geo && !out.latitude) {
                  if (geo.latitude != null) out.latitude = String(geo.latitude);
                  if (geo.longitude != null) out.longitude = String(geo.longitude);
                }
                if (!out.latitude && obj.latitude != null && obj.longitude != null) {
                  out.latitude = String(obj.latitude);
                  out.longitude = String(obj.longitude);
                }
                if (!out.opening_hours) {
                  const oh = obj.openingHours || obj.opening_hours;
                  if (typeof oh === 'string' && oh.trim()) out.opening_hours = oh.trim();
                  else if (Array.isArray(oh) && oh.length)
                    out.opening_hours = oh.map(String).join(' | ');
                  const spec = obj.openingHoursSpecification;
                  if (!out.opening_hours && spec) out.opening_hours = fmtSpec(spec);
                }
                for (const v of Object.values(obj)) {
                  if (v && typeof v === 'object') apply(v);
                }
              };

              for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
                try { apply(JSON.parse(s.textContent)); } catch (e) {}
              }

              // Rare: embedded init blob may carry place lat/lng / rating counts.
              try {
                const scripts = Array.from(document.querySelectorAll('script'))
                  .map(s => s.textContent || '').filter(t => t.includes('reviewCount')
                    || t.includes('latitude') || t.includes('APP_INITIALIZATION')
                    || t.includes('openingHours'));
                for (const t of scripts.slice(0, 8)) {
                  if (!out.review_count) {
                    const m = t.match(/"reviewCount"\s*:\s*"?([\d,]+)"?/i)
                      || t.match(/"ratingCount"\s*:\s*"?([\d,]+)"?/i);
                    if (m) out.review_count = dig(m[1]);
                  }
                  if (!out.latitude) {
                    const m = t.match(/!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)/);
                    if (m) { out.latitude = m[1]; out.longitude = m[2]; }
                  }
                }
              } catch (e) {}

              return out;
            }"""
        ) or {}
    except Exception:
        return {}


def scrape_place(page, url: str, pacer: Pacer, log) -> dict | None:
    try:
        page.goto(url, timeout=45000, wait_until="commit")
    except Exception as e:
        log(f"failed to open place: {_short_err(e)}")
        return None

    try:
        page.wait_for_selector("h1", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(pacer.ms(800, 1600))

    name = _text_or_empty(page, "h1")
    if not name:
        return None

    address = _strip_icon_label(
        _attr_or_empty(page, 'button[data-item-id="address"]', "aria-label"), "Address:")

    phone = _strip_icon_label(
        _attr_or_empty(page, 'button[data-item-id^="phone:tel:"]', "aria-label"), "Phone:")
    if not phone:
        raw = _attr_or_empty(page, 'button[data-item-id^="phone:tel:"]', "data-item-id")
        if raw.startswith("phone:tel:"):
            phone = raw.replace("phone:tel:", "")

    website = _attr_or_empty(page, 'a[data-item-id="authority"]', "href")
    if not website:
        website = _attr_or_empty(page, 'a[aria-label^="Website:"]', "href")

    review_count = _extract_review_count(page)
    category = _extract_category(page)
    opening_hours = _extract_opening_hours(page, pacer)

    # Final URL after the place panel settles usually has !3d/!4d pin coords.
    final_url = ""
    try:
        final_url = page.url or ""
    except Exception:
        final_url = ""
    latitude, longitude = _parse_coords_from_url(final_url)
    if not latitude:
        latitude, longitude = _parse_coords_from_url(url)

    meta = _extract_place_meta_js(page)
    if not review_count:
        review_count = (meta.get("review_count") or "").strip()
    if not category:
        category = (meta.get("category") or "").strip()
    if not latitude:
        latitude = (meta.get("latitude") or "").strip()
        longitude = (meta.get("longitude") or "").strip()
    if not opening_hours:
        opening_hours = (meta.get("opening_hours") or "").strip()

    return {
        "name": name,
        "address": address,
        "phone": phone,
        "website": website,
        "review_count": review_count,
        "category": category,
        "latitude": latitude,
        "longitude": longitude,
        "opening_hours": opening_hours,
        "maps_url": final_url or url,
    }


# ----------------------------------------------------------------------------
# CAPTCHA solving (2captcha / CaptchaAI — both use the 2captcha-style API)
# ----------------------------------------------------------------------------


class CaptchaSolver:
    """Solves Google reCAPTCHA v2 via a 2captcha-compatible service. CaptchaAI
    exposes the same in.php/res.php API, so one client covers both."""

    HOSTS = {
        "2captcha": "https://2captcha.com",
        "captchaai": "https://ocr.captchaai.com",
    }

    def __init__(self, provider: str, api_key: str, host: str | None = None,
                 poll_timeout: int = 180):
        self.provider = provider
        self.api_key = api_key
        self.host = (host or self.HOSTS.get(provider, self.HOSTS["2captcha"])).rstrip("/")
        self.poll_timeout = poll_timeout

    def get_balance(self) -> float:
        """Return account balance. Raises RuntimeError with the API's error code
        (e.g. ERROR_WRONG_USER_KEY) if the key is rejected."""
        import requests
        r = requests.get(
            f"{self.host}/res.php",
            params={"key": self.api_key, "action": "getbalance", "json": 1},
            timeout=30,
        ).json()
        if str(r.get("status")) == "1":
            try:
                return float(r.get("request"))
            except (TypeError, ValueError):
                return 0.0
        raise RuntimeError(str(r.get("request")))

    def validate(self, log=print):
        """Check the key up-front. Fatal only for clear key errors; a missing
        getbalance endpoint is tolerated (some emulators don't expose it)."""
        try:
            bal = self.get_balance()
            log(f"Captcha       : {self.provider} OK — balance {bal}")
            if bal <= 0:
                log(f"[warn] captcha balance is {bal}; solving may fail (top up).")
        except RuntimeError as e:
            msg = str(e)
            if "KEY" in msg.upper():   # WRONG_USER_KEY / KEY_DOES_NOT_EXIST
                raise SystemExit(
                    f"[fatal] {self.provider} rejected the API key ({msg}). "
                    f"Get your key from your dashboard (e.g. https://captchaai.com/config).")
            log(f"[warn] could not verify captcha balance ({msg}); continuing.")
        except Exception as e:
            log(f"[warn] captcha balance check failed ({_short_err(e)}); continuing.")

    def solve_recaptcha_v2(self, sitekey: str, page_url: str) -> str:
        import requests
        submit = requests.get(
            f"{self.host}/in.php",
            params={"key": self.api_key, "method": "userrecaptcha",
                    "googlekey": sitekey, "pageurl": page_url, "json": 1},
            timeout=30,
        ).json()
        if str(submit.get("status")) != "1":
            raise RuntimeError(f"captcha submit rejected: {submit.get('request')}")
        rid = submit["request"]

        deadline = time.time() + self.poll_timeout
        while time.time() < deadline:
            time.sleep(5)
            res = requests.get(
                f"{self.host}/res.php",
                params={"key": self.api_key, "action": "get", "id": rid, "json": 1},
                timeout=30,
            ).json()
            if str(res.get("status")) == "1":
                return res["request"]
            if res.get("request") != "CAPCHA_NOT_READY":
                raise RuntimeError(f"captcha error: {res.get('request')}")
        raise TimeoutError("captcha solve timed out")


class CaptchaSolverChain:
    """Try primary captcha provider, then backup on failure."""

    def __init__(self, solvers: list):
        self.solvers = [s for s in solvers if s is not None]
        self.provider = "+".join(s.provider for s in self.solvers) if self.solvers else "none"

    def validate(self, log=print):
        for s in self.solvers:
            try:
                s.validate(log=log)
            except SystemExit:
                if len(self.solvers) == 1:
                    raise
                log(f"[warn] captcha provider {s.provider} failed validation; will try backup if needed")

    def solve_recaptcha_v2(self, sitekey: str, page_url: str) -> str:
        last_err: Exception | None = None
        for i, s in enumerate(self.solvers):
            try:
                if i > 0:
                    # best-effort log via print; callers usually pass their own log to validate
                    print(f"[captcha] primary failed — trying backup {s.provider}")
                return s.solve_recaptcha_v2(sitekey, page_url)
            except Exception as e:
                last_err = e
                continue
        raise last_err or RuntimeError("no captcha solvers configured")


def detect_recaptcha_sitekey(page) -> str | None:
    """Return the reCAPTCHA site key rendered on the page, if any."""
    try:
        el = page.query_selector("[data-sitekey]")
        if el:
            key = el.get_attribute("data-sitekey")
            if key:
                return key
        for frame in page.query_selector_all("iframe[src*='recaptcha']"):
            src = frame.get_attribute("src") or ""
            m = re.search(r"[?&]k=([^&]+)", src)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def page_looks_blocked(page) -> bool:
    url = (page.url or "").lower()
    if "/sorry/" in url or "ipv4.google" in url:
        return True
    try:
        txt = (page.content() or "").lower()
    except Exception:
        txt = ""
    return "unusual traffic" in txt or "detected unusual" in txt


def _solve_and_submit(page, solver: CaptchaSolver, sitekey: str, log) -> bool:
    """Solve one reCAPTCHA, inject the token, and submit. Returns True if a
    token was obtained and injected (NOT a guarantee Google accepted it)."""
    try:
        token = solver.solve_recaptcha_v2(sitekey, page.url)
    except Exception as e:
        log(f"captcha solve failed: {_short_err(e)}")
        return False
    try:
        page.evaluate(
            """(tok) => {
                let t = document.getElementById('g-recaptcha-response');
                if (!t) {
                    t = document.createElement('textarea');
                    t.id = 'g-recaptcha-response';
                    t.name = 'g-recaptcha-response';
                    t.style.display = 'block';
                    document.body.appendChild(t);
                }
                t.value = tok;
                // fire any registered reCAPTCHA callback so the site "sees" it
                try {
                    if (window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients) {
                        Object.values(window.___grecaptcha_cfg.clients).forEach(c => {
                            Object.values(c).forEach(o => {
                                if (o && o.callback) { try { o.callback(tok); } catch(e){} }
                            });
                        });
                    }
                } catch (e) {}
            }""",
            token,
        )
        page.evaluate("() => { const f = document.querySelector('form'); if (f) f.submit(); }")
    except Exception as e:
        log(f"captcha token injection issue: {_short_err(e)}")
    page.wait_for_timeout(4000)
    return True


def page_has_captcha(page) -> bool:
    return bool(detect_recaptcha_sitekey(page)) or page_looks_blocked(page)


def solve_captcha_if_present(page, solver: CaptchaSolver | None, log) -> bool:
    """Single-attempt: detect a reCAPTCHA and, if a solver is set, solve+inject.
    Returns True if a captcha was handled (a solve was attempted)."""
    if solver is None:
        return False
    sitekey = detect_recaptcha_sitekey(page)
    if not sitekey:
        if page_looks_blocked(page):
            log("blocked page detected but no reCAPTCHA sitekey — cannot auto-solve")
        return False
    log(f"captcha detected — solving via {solver.provider}")
    ok = _solve_and_submit(page, solver, sitekey, log)
    if ok:
        log("captcha token submitted")
    return ok


def handle_captcha_with_retries(page, solver, url, nav_timeout, pacer, retries, log) -> bool:
    """Solve the captcha and VERIFY it actually cleared; retry up to `retries`
    extra times if it didn't (wrong/expired token or a fresh captcha). Returns
    True if the page ends up captcha-free, False if it couldn't be cleared."""
    if solver is None:
        return True  # nothing to do

    attempts = max(1, retries + 1)
    for i in range(attempts):
        sitekey = detect_recaptcha_sitekey(page)
        if not sitekey:
            if page_looks_blocked(page):
                log("blocked page but no reCAPTCHA sitekey — cannot auto-solve")
                return False
            return True  # page is clear — no captcha

        log(f"captcha detected (attempt {i + 1}/{attempts}) — solving via {solver.provider}")
        if _solve_and_submit(page, solver, sitekey, log):
            log("token submitted; reloading to verify")
            robust_goto(page, url, nav_timeout, log)
            handle_consent(page)
            page.wait_for_timeout(pacer.ms(1500, 3000))
            if not page_has_captcha(page):
                log("captcha cleared")
                return True
            log("captcha still present after solve")
        # solve failed or didn't clear — small pause before retrying
        pacer.short()

    log(f"captcha NOT cleared after {attempts} attempt(s) — skipping this search")
    return False


# ----------------------------------------------------------------------------
# Progress reporting
# ----------------------------------------------------------------------------


def fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


class ProgressTracker:
    """Thread-safe aggregate progress line printed as jobs finish: how many of
    the total jobs are done, percentage, rows so far, elapsed time and ETA."""

    def __init__(self, total: int, rows_fn, every: int = 1, start_done: int = 0,
                 on_tick=None):
        self.total = max(1, total)
        self.rows_fn = rows_fn
        self.every = max(1, every)
        self.offset = start_done          # jobs already done before this run (resume)
        self.done = start_done
        self.start = time.time()
        self._lock = threading.Lock()
        self.on_tick = on_tick

    def tick(self, label: str = ""):
        with self._lock:
            self.done += 1
            done = self.done
        this_run = done - self.offset
        if self.on_tick:
            try:
                self.on_tick(this_run, self.rows_fn())
            except Exception:
                pass
        if done != self.total and this_run % self.every != 0:
            return
        elapsed = time.time() - self.start
        rate = (done - self.offset) / elapsed if elapsed > 0 else 0  # this run's pace
        eta = (self.total - done) / rate if rate > 0 else 0
        pct = 100.0 * done / self.total
        tail = f" | last: {label}" if label else ""
        print(f"[progress] {done}/{self.total} jobs ({pct:.1f}%) | "
              f"rows {self.rows_fn()} | elapsed {fmt_duration(elapsed)} | "
              f"ETA {fmt_duration(eta)}{tail}", flush=True)

    def snapshot(self) -> dict:
        """Current stats for external reporting (e.g. a Telegram bot)."""
        done = self.done
        elapsed = time.time() - self.start
        rate = (done - self.offset) / elapsed if elapsed > 0 else 0
        eta = (self.total - done) / rate if rate > 0 else 0
        return {
            "done": done, "total": self.total,
            "pct": 100.0 * done / self.total,
            "rows": self.rows_fn(),
            "elapsed_s": elapsed, "eta_s": eta,
            "rate_per_min": rate * 60,
        }


# ----------------------------------------------------------------------------
# Worker
# ----------------------------------------------------------------------------


@dataclass
class Job:
    keyword: str
    location: str


class Runner:
    def __init__(self, args, proxies: list[Proxy], writer: CsvWriter, pacer: Pacer,
                 solver: CaptchaSolver | None = None, session: "SessionState | None" = None,
                 progress: "ProgressTracker | None" = None):
        self.args = args
        self.proxies = proxies
        self.writer = writer
        self.pacer = pacer
        self.solver = solver
        self.session = session          # kept for API compat; not used per-job
        self.progress = progress
        self.failed_log = None          # set by main()
        self.stop_event = threading.Event()   # bot /stop sets this
        self._proxy_idx = 0
        self._proxy_lock = threading.Lock()
        self._counter_lock = threading.Lock()
        self.rows_written = 0

    def next_proxy(self) -> Proxy | None:
        if not self.proxies:
            return None
        with self._proxy_lock:
            p = self.proxies[self._proxy_idx % len(self.proxies)]
            self._proxy_idx += 1
        return p

    def _pick_working_proxy(self, log):
        """Rotate through proxies, preflighting each, until one can reach Google.
        Returns (proxy, ok). With no proxies, returns (None, True)."""
        if not self.proxies:
            return None, True
        attempts = min(self.args.proxy_attempts, len(self.proxies))
        for _ in range(attempts):
            proxy = self.next_proxy()
            if self.args.no_preflight:
                return proxy, True
            ok, detail = preflight_proxy(proxy, self.args.preflight_timeout)
            if ok:
                log(f"proxy {proxy.label()} OK — {detail}")
                return proxy, True
            log(f"proxy {proxy.label()} unusable — {detail}; rotating")
        return proxy, False

    def _log(self, job: Job, msg: str):
        print(f"[{job.keyword} | {job.location}] {msg}", flush=True)

    def run_job(self, job: Job):
        """Try the job, rotating to a fresh proxy on ANY failure — a dead proxy
        (fails preflight) OR a proxy that passes preflight but can't load the
        page / clear a captcha — up to --proxy-attempts attempts total."""
        if _SHUTDOWN.is_set() or self.stop_event.is_set():
            return   # do not start new work during shutdown / stop
        log = lambda m: self._log(job, m)
        attempts = max(1, self.args.proxy_attempts) if self.proxies else 1
        completed = False
        last_reason = "error"

        for attempt in range(1, attempts + 1):
            if _SHUTDOWN.is_set() or self.stop_event.is_set():
                return
            proxy = self.next_proxy() if self.proxies else None
            label = proxy.label() if proxy else "no-proxy"

            # Cheap liveness check first; a dead proxy just consumes an attempt.
            if proxy is not None and not self.args.no_preflight:
                okp, detail = preflight_proxy(proxy, self.args.preflight_timeout)
                if not okp:
                    log(f"proxy {label} unusable — {detail} "
                        f"(attempt {attempt}/{attempts}); rotating")
                    last_reason = f"proxy dead: {detail}"
                    continue
                log(f"proxy {label} OK — {detail}")

            log(f"start (attempt {attempt}/{attempts}, engine={self.args.engine}, "
                f"proxy={label})")
            ok, reason = self._scrape_once(job, proxy, log)
            if ok:
                completed = True
                break
            last_reason = reason
            if attempt < attempts:
                log(f"attempt {attempt} failed ({reason}); retrying with another proxy")

        if not completed:
            log(f"JOB FAILED after {attempts} attempt(s): {last_reason}")
            self._record_fail(job, last_reason)
        self.pacer.between_jobs()
        log("done")
        if self.progress:
            self.progress.tick(f"{job.keyword} | {job.location}")

    def _scrape_once(self, job: Job, proxy, log) -> tuple[bool, str]:
        """One browser attempt with a given proxy. Returns (success, reason)."""
        nav_t = self.args.nav_timeout
        try:
            with BrowserSession(self.args.engine, proxy, self.args.headless,
                                stealth=not self.args.no_stealth,
                                browser_path=self.args.browser_path,
                                geoip=self.args.geoip,
                                block_resources=self.args.block_resources) as bs:
                page = bs.page
                # Fresh keyword/city -> flush any cache/cookies/storage first.
                bs.clear_browser_data()
                log("browser cache/cookies/storage flushed for new search")

                url = build_search_url(job.keyword, job.location)
                if not robust_goto(page, url, nav_t, log):
                    return False, "nav timeout / proxy could not load Maps"
                handle_consent(page)
                # If Google throws a captcha / "unusual traffic" wall, solve it
                # and verify it cleared — retrying up to --captcha-retries times.
                if self.solver is not None and page_has_captcha(page):
                    if not handle_captcha_with_retries(
                            page, self.solver, url, nav_t, self.pacer,
                            self.args.captcha_retries, log):
                        return False, "captcha not cleared"
                page.wait_for_timeout(self.pacer.ms(1500, 3000))

                place_urls = scroll_results(page, self.args.max_results, self.pacer, log)
                log(f"found {len(place_urls)} listings")

                for i, purl in enumerate(place_urls, 1):
                    try:
                        record = scrape_place(page, purl, self.pacer, log)
                    except Exception as e:
                        log(f"scrape error on listing {i}: {_short_err(e)}")
                        continue
                    if not record:
                        continue

                    enrich = {k: "" for k in ["email"] + list(SOCIAL_PATTERNS.keys())}
                    if record.get("website") and self.args.do_website_scrape:
                        try:
                            enrich = enrich_from_website(record["website"], proxy, bs.ua)
                        except Exception as e:
                            log(f"enrich error: {_short_err(e)}")

                    row = {"keyword": job.keyword, "query_location": job.location,
                           **record, **enrich}
                    if self.writer.write(job.location, row):
                        with self._counter_lock:
                            self.rows_written += 1
                        log(f"saved: {record['name']}")
                    else:
                        log(f"duplicate skipped: {record['name']}")

                    self.pacer.tick(log)            # periodic cool-off
                    self.pacer.between_listings()   # random human pause

                return True, ""   # scroll+scrape finished normally
        except Exception as e:
            if self.args.debug:
                traceback.print_exc()
            return False, _short_err(e)

    def _record_fail(self, job: Job, reason: str):
        if self.failed_log is not None:
            self.failed_log.add(job.keyword, job.location, reason)


# ----------------------------------------------------------------------------
# Self-test (no Google traffic) — verify engine, stealth, cache flush
# ----------------------------------------------------------------------------


def run_selftest(args) -> int:
    print(f"[selftest] host OS={HOST_OS} engine={args.engine} headless={args.headless}")
    proxy = None
    if args.engine == "camoufox":
        print("[selftest] note: camoufox works with authenticated or plain proxies.")
    if args.engine == "brave":
        detected = args.browser_path or detect_brave_path()
        print(f"[selftest] Brave binary: {detected or 'NOT FOUND'}")
    try:
        with BrowserSession(args.engine, proxy, args.headless,
                            stealth=not args.no_stealth,
                            browser_path=args.browser_path) as bs:
            page = bs.page
            target = bs.exec_path or BrowserSession._CHANNELS.get(args.engine, "bundled-chromium")
            print(f"[selftest] browser launched OK "
                  f"(engine={args.engine}, target={target}, ua={bs.ua[:40]}...)")
            # Load a data page (no network) and check stealth values.
            page.set_content("<html><body><h1>selftest</h1></body></html>")
            wd = page.evaluate("() => navigator.webdriver")
            langs = page.evaluate("() => navigator.languages")
            plugins = page.evaluate("() => navigator.plugins.length")
            hw = page.evaluate("() => navigator.hardwareConcurrency")
            has_chrome = page.evaluate("() => !!window.chrome")
            plat = page.evaluate("() => navigator.platform")
            print(f"[selftest] navigator.webdriver = {wd}  (expect None/undefined)")
            print(f"[selftest] navigator.languages = {langs}")
            print(f"[selftest] navigator.plugins   = {plugins} (expect > 0)")
            print(f"[selftest] hardwareConcurrency = {hw}")
            print(f"[selftest] window.chrome       = {has_chrome}")
            print(f"[selftest] navigator.platform  = {plat} "
                  f"(matches UA: {plat == ua_platform(bs.ua)})")
            bs.clear_browser_data()
            print("[selftest] cache/cookies/storage flush OK")
            ok = (wd in (None, False)) and plugins and has_chrome
            print(f"[selftest] RESULT: {'PASS' if ok else 'CHECK ABOVE'}")
            return 0 if ok else 1
    except Exception as e:
        print(f"[selftest] FAILED to launch/run: {e}")
        traceback.print_exc()
        print("\nIf this is a bare VPS, install the engine first:")
        print("  chrome  : python -m playwright install chromium && python -m playwright install-deps")
        print("  camoufox: python -m camoufox fetch")
        return 2


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Multi-threaded Google Maps business scraper.")
    p.add_argument("--engine",
                   choices=["chrome", "google-chrome", "edge", "brave", "camoufox"],
                   default="chrome",
                   help="Browser engine. 'chrome' = bundled Chromium (most "
                        "reliable); 'google-chrome'/'edge' = real Chrome/Edge; "
                        "'brave' = Brave; 'camoufox' = stealth Firefox. Setup "
                        "auto-installs whichever browser the engine needs. "
                        "Proxies may be authenticated or not, for any engine.")
    p.add_argument("--browser-path", default=None, dest="browser_path",
                   help="Explicit path to a Chromium-based browser binary "
                        "(Brave/Chrome/Chromium). Overrides auto-detection.")
    p.add_argument("--proxies", default="proxies.txt")
    p.add_argument("--locations", default="locations.txt")
    p.add_argument("--keywords", default="keywords.txt")
    p.add_argument("--output-dir", default=None, dest="output_dir",
                   help="Folder for the per-location CSV files. One file per "
                        "city/state/country, named '<Location>_<date_time>.csv'. "
                        "Default 'results'.")
    p.add_argument("--output", default=None,
                   help="(Deprecated) Old single-file option. If given, its "
                        "folder is used as --output-dir. Output is always one "
                        "CSV per location now.")
    p.add_argument("--threads", type=int, default=2,
                   help="Concurrent workers. Each = one full browser "
                        "(~300-600MB RAM). Keep low on small VPS. Default 2.")
    p.add_argument("--chunk-size", type=int, default=500, dest="chunk_size",
                   help="Process jobs in bounded chunks of this size so memory/CPU "
                        "stay flat at any scale (millions of jobs). Progress is "
                        "checkpointed after each chunk. Default 500.")
    p.add_argument("--block-resources", choices=["none", "images", "media", "all"],
                   default="media", dest="block_resources",
                   help="Abort heavy sub-resources to save RAM/CPU/bandwidth. "
                        "'media'=images+media+fonts (default), 'all' also blocks "
                        "CSS, 'none' loads everything.")
    p.add_argument("--max-results", type=int, default=0, dest="max_results",
                   help="Max listings per keyword+location. 0 = unlimited "
                        "(load every business Google returns). Default 0.")

    # navigation / proxy reliability
    p.add_argument("--nav-timeout", type=int, default=45, dest="nav_timeout",
                   help="Seconds to wait for a page to start loading. Default 45.")
    p.add_argument("--proxy-attempts", type=int, default=3, dest="proxy_attempts",
                   help="How many proxies to try per job before giving up. "
                        "Rotates on a dead proxy (preflight) AND when a live "
                        "proxy can't load the page / clear a captcha. Default 3.")
    p.add_argument("--preflight-timeout", type=float, default=12.0,
                   dest="preflight_timeout",
                   help="Seconds for the proxy reachability check. Default 12.")
    p.add_argument("--no-preflight", action="store_true", dest="no_preflight",
                   help="Skip the pre-launch proxy reachability check.")
    p.add_argument("--check-proxies", action="store_true", dest="check_proxies",
                   help="Test every proxy against Google (requests) and report, then exit.")
    p.add_argument("--diagnose", action="store_true", dest="diagnose",
                   help="Deep test ONE proxy in a real browser (neutral page -> "
                        "Google Maps) and report exactly where it breaks, then exit.")
    p.add_argument("--proxy-index", type=int, default=0, dest="proxy_index",
                   help="Which proxy (0-based line) to use for --diagnose. Default 0.")
    p.add_argument("--geoip", action="store_true", dest="geoip",
                   help="Camoufox only: enable exit-IP geolocation spoofing "
                        "(needs proxies that allow an IP lookup). Off by default.")

    # headless is the default (works on a bare VPS). --headed needs xvfb.
    p.add_argument("--headless", dest="headless", action="store_true", default=True)
    p.add_argument("--headed", dest="headless", action="store_false",
                   help="Show the browser (needs a display or xvfb-run).")

    # pacing / ban protection
    p.add_argument("--min-delay", type=float, default=2.0, dest="min_delay",
                   help="Min seconds between listings.")
    p.add_argument("--max-delay", type=float, default=5.0, dest="max_delay",
                   help="Max seconds between listings.")
    p.add_argument("--cooldown-every", type=int, default=25, dest="cooldown_every",
                   help="Force a long pause after this many requests (0=off).")
    p.add_argument("--cooldown-min", type=float, default=25.0, dest="cooldown_min")
    p.add_argument("--cooldown-max", type=float, default=60.0, dest="cooldown_max")

    # captcha auto-solving
    p.add_argument("--captcha-provider", choices=["none", "2captcha", "captchaai"],
                   default="none", dest="captcha_provider",
                   help="Auto-solve Google reCAPTCHA with 2captcha or CaptchaAI.")
    p.add_argument("--captcha-key", default=None, dest="captcha_key",
                   help="API key for the captcha provider.")
    p.add_argument("--captcha-host", default=None, dest="captcha_host",
                   help="Override the provider API host (2captcha-compatible).")
    p.add_argument("--captcha-retries", type=int, default=2, dest="captcha_retries",
                   help="Extra times to re-solve if the captcha isn't accepted "
                        "(wrong/expired token or a fresh challenge). Default 2.")
    p.add_argument("--captcha-backup-provider", choices=["none", "2captcha", "captchaai"],
                   default="none", dest="captcha_backup_provider",
                   help="Backup captcha provider if primary fails.")
    p.add_argument("--captcha-backup-key", default=None, dest="captcha_backup_key",
                   help="API key for the backup captcha provider.")
    p.add_argument("--captcha-backup-host", default=None, dest="captcha_backup_host",
                   help="Override host for the backup captcha provider.")

    p.add_argument("--no-stealth", action="store_true", help="Disable stealth injection.")
    p.add_argument("--scrape-websites", choices=["yes", "no"], default="yes",
                   dest="scrape_websites",
                   help="Visit each business's website (from Google) to extract "
                        "email + social links. 'no' = Google Maps data only. "
                        "Default yes.")
    p.add_argument("--no-enrich", action="store_true",
                   help="Alias for --scrape-websites no.")
    p.add_argument("--no-proxy", action="store_true", dest="no_proxy",
                   help="Run without proxies (direct connection). Useful for a "
                        "quick local test. Not recommended for large runs.")
    p.add_argument("--selftest", action="store_true",
                   help="Launch engine, verify stealth + cache flush, then exit.")
    p.add_argument("--fresh", action="store_true", dest="fresh",
                   help="Ignore any saved session and start over (new files). "
                        "By default the run auto-resumes from where it stopped.")

    # cluster / mode
    p.add_argument("--config", default=None,
                   help="Path to config.json (role, telegram, cluster, users, "
                        "scrape defaults). Default config.json.")
    p.add_argument("--setup", action="store_true",
                   help="Run the interactive first-run setup wizard and save config.")
    p.add_argument("--role", choices=["standalone", "coordinator",
                                      "coordinator+worker", "worker"], default=None,
                   help="Override the role for this machine (else uses config).")

    # Telegram bot mode
    p.add_argument("--bot", action="store_true",
                   help="Run as a single-machine Telegram bot (alias for the "
                        "coordinator+worker role on this host).")
    p.add_argument("--bot-config", default="bot_config.json", dest="bot_config",
                   help="Path to the bot config JSON (token, allowed_users, "
                        "scrape settings). Default bot_config.json.")
    p.add_argument("--telegram-token", default=None, dest="telegram_token",
                   help="Telegram bot token (overrides the config file).")
    p.add_argument("--telegram-users", default=None, dest="telegram_users",
                   help="Comma-separated allowed Telegram user IDs (overrides config).")
    p.add_argument("--skip-setup", action="store_true", dest="skip_setup",
                   help="Do not auto-install dependencies / browser on startup.")
    p.add_argument("--force-setup", action="store_true", dest="force_setup",
                   help="Re-run dependency/browser setup even if already done.")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args(argv)
    # Whether to visit business websites for email/socials.
    args.do_website_scrape = (args.scrape_websites == "yes") and not args.no_enrich
    # Resolve the output directory (per-location CSVs live here).
    if args.output_dir:
        out = args.output_dir
    elif args.output:
        # Back-compat: derive a folder from an old --output path.
        d = os.path.dirname(args.output)
        base = os.path.basename(args.output)
        if base.lower().endswith(".csv"):
            base = base[:-4]
        out = os.path.join(d, base) if d else (base or "results")
    else:
        out = "results"
    args.output_dir = out
    return args


def run_diagnose(args) -> int:
    """Deep-test ONE proxy inside a REAL browser to pinpoint why Maps fails.

    Distinguishes 'the browser can't use the proxy at all' from 'the browser
    reaches the internet but Google Maps blocks this IP' — the two look
    identical in the normal run but need opposite fixes."""
    proxy = None
    if not args.no_proxy:
        proxies = load_proxies(args.proxies, require_auth=False)
        idx = max(0, min(args.proxy_index, len(proxies) - 1))
        proxy = proxies[idx]
        print(f"Diagnosing proxy #{idx}: {proxy.label()}  (engine={args.engine})\n")
    else:
        print(f"Diagnosing with NO proxy (direct)  (engine={args.engine})\n")

    # Step 1 — can `requests` reach Google + what is the exit IP?
    print("[1] requests -> Google generate_204 ...")
    ok, detail = preflight_proxy(proxy, args.preflight_timeout)
    print(f"    {'OK' if ok else 'FAIL'}: {detail}")
    if proxy is not None:
        try:
            import requests
            ip = requests.get("https://api.ipify.org", proxies=proxy.as_requests(),
                              timeout=args.preflight_timeout).text.strip()
            print(f"    exit IP via requests: {ip}")
        except Exception as e:
            print(f"    exit IP lookup failed: {_short_err(e)}")

    nav_t = args.nav_timeout
    try:
        with BrowserSession(args.engine, proxy, args.headless,
                            stealth=not args.no_stealth,
                            browser_path=args.browser_path, geoip=args.geoip,
                            block_resources=args.block_resources) as bs:
            page = bs.page

            # Step 2 — can the BROWSER use the proxy at all (neutral site)?
            print("\n[2] browser -> neutral site (api.ipify.org) through proxy ...")
            neutral_ok = robust_goto(page, "https://api.ipify.org/?format=text", nav_t,
                                     lambda m: print("    " + m), retries=1)
            browser_ip = ""
            if neutral_ok:
                try:
                    browser_ip = (page.inner_text("body", timeout=8000) or "").strip()[:60]
                except Exception:
                    pass
                print(f"    OK: browser loaded a page through the proxy "
                      f"(exit IP seen: {browser_ip or 'n/a'})")
            else:
                print("    FAIL: the browser cannot load ANY page through this proxy.")

            # Step 3 — Google Maps itself.
            print("\n[3] browser -> Google Maps search through proxy ...")
            url = build_search_url("coffee shop", "Austin, Texas, USA")
            maps_ok = robust_goto(page, url, nav_t, lambda m: print("    " + m), retries=1)
            title = feed = ""
            blocked = False
            if maps_ok:
                try:
                    title = page.title()
                except Exception:
                    pass
                try:
                    page.wait_for_selector('div[role="feed"]', timeout=15000)
                    feed = "results feed FOUND"
                except Exception:
                    feed = "results feed NOT found"
                blocked = page_looks_blocked(page) or bool(detect_recaptcha_sitekey(page))
                print(f"    loaded. title={title!r}; {feed}; "
                      f"captcha/block={'YES' if blocked else 'no'}")

            # Verdict
            print("\n" + "=" * 60)
            print("VERDICT:")
            if proxy is not None and not ok:
                print("  • Proxy can't even pass a basic requests check — bad proxy/credentials.")
            elif not neutral_ok:
                print("  • The BROWSER can't use the proxy (requests works but the browser")
                print("    doesn't). Likely a proxy-auth/QUIC/config issue rather than Google.")
                print("    Try: --engine chrome, ensure creds are right, keep --disable-quic.")
            elif neutral_ok and not maps_ok:
                print("  • Browser reaches the internet via the proxy, but Google MAPS")
                print("    times out/hangs for this IP → Google is blocking this datacenter")
                print("    IP at the Maps layer. No code change fixes this — use residential")
                print("    or mobile proxies.")
            elif maps_ok and blocked:
                print("  • Maps loaded but served a CAPTCHA / 'unusual traffic' wall for this")
                print("    IP → datacenter IP flagged. Use residential proxies, or configure")
                print("    --captcha-provider/--captcha-key to auto-solve.")
            elif maps_ok and "FOUND" in feed:
                print("  • Everything works! Results feed loaded. A normal run should scrape.")
            else:
                print("  • Maps loaded but no results feed and no obvious block — Google may")
                print("    have changed its DOM, or the region returned no results. Re-run with")
                print("    --headed --debug to watch.")
            print("=" * 60)
    except Exception as e:
        print(f"\n[diagnose] browser error: {_short_err(e)}")
        if args.debug:
            traceback.print_exc()
        return 2
    return 0


def run_check_proxies(args) -> int:
    """Test every proxy against Google concurrently and print a report."""
    proxies = load_proxies(args.proxies, require_auth=False)
    print(f"Testing {len(proxies)} proxies against {PROXY_TEST_URL} "
          f"(timeout {args.preflight_timeout}s)...\n")
    results = []

    def check(p):
        ok, detail = preflight_proxy(p, args.preflight_timeout)
        return p, ok, detail

    with ThreadPoolExecutor(max_workers=min(20, len(proxies))) as pool:
        for p, ok, detail in pool.map(check, proxies):
            tag = "OK  " if ok else "FAIL"
            print(f"  [{tag}] {p.label():24} {detail}")
            results.append(ok)

    good = sum(results)
    print("-" * 60)
    print(f"Working: {good}/{len(proxies)}   Failing: {len(proxies) - good}/{len(proxies)}")
    if good == 0:
        print("None of your proxies can reach Google. These look like datacenter "
              "IPs, which Google Maps frequently blocks. Residential/mobile "
              "proxies will work far better.")
    return 0


# ----------------------------------------------------------------------------
# Telegram bot mode
# ----------------------------------------------------------------------------


class BotConfig:
    def __init__(self, path, token, allowed_users, notify_interval, scrape):
        self.path = path
        self.token = token
        self.allowed_users = [int(u) for u in allowed_users]
        self.notify_interval = int(notify_interval)
        self.scrape = scrape or {}

    def save(self):
        data = {"token": self.token, "allowed_users": self.allowed_users,
                "notify_interval_sec": self.notify_interval, "scrape": self.scrape}
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass


def load_bot_config(args) -> BotConfig:
    path = args.bot_config or "bot_config.json"
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            raise SystemExit(f"[fatal] could not read bot config {path}: {_short_err(e)}")
    token = args.telegram_token or data.get("token")
    users = data.get("allowed_users", []) or []
    if args.telegram_users:
        users = [int(x) for x in re.split(r"[,\s]+", args.telegram_users)
                 if x.strip().lstrip("-").isdigit()]
    if not token:
        raise SystemExit(
            "[fatal] no Telegram bot token. Put it in bot_config.json "
            "(\"token\": \"...\") or pass --telegram-token. Create a bot with "
            "@BotFather to get a token.")
    return BotConfig(path, token, users, data.get("notify_interval_sec", 300),
                     data.get("scrape", {}))


class RunState:
    """Shared live state for the currently-running (or idle) scrape."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.thread = None
        self.runner = self.progress = self.session = None
        self.writer = self.failed_log = None
        self.total_jobs = 0
        self.args = self.engine = self.output_dir = None
        self.chat_id = self.started_by = None
        self.start_time = None

    def attach(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)


class TelegramBot:
    """Minimal Telegram Bot API client over long polling (via requests)."""

    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.offset = None

    def get_updates(self, timeout=25):
        import requests
        params = {"timeout": timeout}
        if self.offset is not None:
            params["offset"] = self.offset
        try:
            r = requests.get(f"{self.base}/getUpdates", params=params,
                             timeout=timeout + 15)
            data = r.json()
        except Exception:
            return []
        if not data.get("ok"):
            return []
        updates = data.get("result", [])
        if updates:
            self.offset = updates[-1]["update_id"] + 1
        return updates

    def send(self, chat_id, text):
        import requests
        try:
            requests.post(f"{self.base}/sendMessage",
                          data={"chat_id": chat_id, "text": text},
                          timeout=30)
        except Exception:
            pass

    def send_document(self, chat_id, path, caption=""):
        """Send a file (≤ 50 MB per Telegram bot limit). Returns True on success."""
        import requests
        try:
            with open(path, "rb") as fh:
                r = requests.post(
                    f"{self.base}/sendDocument",
                    data={"chat_id": chat_id, "caption": caption[:1000]},
                    files={"document": (os.path.basename(path), fh)},
                    timeout=300,
                )
            return bool(r.json().get("ok"))
        except Exception as e:
            self.send(chat_id, f"⚠️ Could not send file {os.path.basename(path)}: {_short_err(e)}")
            return False

    def get_file_path(self, file_id):
        import requests
        try:
            r = requests.get(f"{self.base}/getFile", params={"file_id": file_id}, timeout=30)
            return r.json()["result"]["file_path"]
        except Exception:
            return None

    def download_file(self, file_path, dest):
        import requests
        try:
            url = self.base.replace("/bot", "/file/bot") + "/" + file_path
            r = requests.get(url, timeout=120)
            with open(dest, "wb") as fh:
                fh.write(r.content)
            return True
        except Exception:
            return False


def _coerce(old, v):
    """Coerce a string value to the type of the existing default."""
    if isinstance(old, bool):
        return str(v).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(old, int) and not isinstance(old, bool):
        try:
            return int(v)
        except ValueError:
            return old
    if isinstance(old, float):
        try:
            return float(v)
        except ValueError:
            return old
    return str(v)


def build_scrape_args(cfg: BotConfig, override_tokens=None):
    """Build a scrape args namespace from defaults + config + inline overrides."""
    a = parse_args([])
    for k, v in (cfg.scrape or {}).items():
        key = str(k).replace("-", "_")
        if hasattr(a, key):
            setattr(a, key, _coerce(getattr(a, key), v))
    for tok in (override_tokens or []):
        if "=" in tok:
            k, v = tok.split("=", 1)
            key = k.strip().lstrip("-").replace("-", "_")
            if hasattr(a, key):
                setattr(a, key, _coerce(getattr(a, key), v))
    a.do_website_scrape = (a.scrape_websites == "yes") and not a.no_enrich
    if not a.output_dir:
        a.output_dir = "results"
    a.bot = False   # the spawned run is not itself a bot
    return a


def _status_text(state: RunState) -> str:
    with state.lock:
        running = state.running
        prog = state.progress
        fl = state.failed_log
        eng = state.engine
        od = state.output_dir
    if not running:
        return "⚪ Idle. Send /run to start a scrape."
    if prog is None:
        return "🟡 Starting up (loading inputs / launching browsers)…"
    s = prog.snapshot()
    failed = fl.count if fl else 0
    return (
        f"🟢 Running ({eng})\n"
        f"Jobs: {s['done']:,}/{s['total']:,} ({s['pct']:.1f}%)\n"
        f"Rows saved: {s['rows']:,}\n"
        f"Failed/skipped: {failed:,}\n"
        f"Rate: {s['rate_per_min']:.1f} jobs/min\n"
        f"Elapsed: {fmt_duration(s['elapsed_s'])}  |  ETA: {fmt_duration(s['eta_s'])}\n"
        f"Output: {od}"
    )


def _completion_text(sargs, stats: dict) -> str:
    if stats.get("completed"):
        head = "✅ Job complete!"
        tail = "Browsers shut down. Send /run to start another job."
    else:
        head = "⏹ Job stopped."
        tail = "Progress saved. Send /run to resume, or /run fresh=yes to restart."
    return (
        f"{head}\n"
        f"Engine: {sargs.engine}\n"
        f"Processed: {stats['processed']:,}/{stats['total_jobs']:,}\n"
        f"Rows saved: {stats['rows']:,}\n"
        f"Failed/skipped: {stats['failed']:,}\n"
        f"Elapsed: {fmt_duration(stats['elapsed_s'])}\n"
        f"Output: {stats['output_dir']}\n"
        f"{tail}"
    )


HELP_TEXT = (
    "Google Maps Scraper bot commands:\n"
    "/run [key=value ...] — start scraping (e.g. /run engine=chrome threads=4)\n"
    "/status — live progress + stats\n"
    "/stop — stop the current job and kill all browsers\n"
    "/users — list authorized users\n"
    "/adduser <id> — authorize another user\n"
    "/removeuser <id> — de-authorize a user\n"
    "/whoami — show your Telegram user id\n"
    "/help — this message"
)


def _start_run(bot: TelegramBot, cfg: BotConfig, state: RunState, chat_id, uid, tokens):
    with state.lock:
        if state.running:
            bot.send(chat_id, "⚠️ A job is already running. Use /status or /stop.")
            return
        state.running = True
        state.chat_id = chat_id
        state.started_by = uid
        state.start_time = time.time()
        state.progress = None
        state.runner = None

    def worker():
        try:
            sargs = build_scrape_args(cfg, tokens)
            bot.send(chat_id, f"▶️ Starting scrape — engine={sargs.engine}, "
                              f"threads={sargs.threads}, output={sargs.output_dir}")
            ensure_dependencies(sargs)      # install browser for this engine if needed
            stats = execute_run(sargs, state=state)
            if stats.get("already_done"):
                bot.send(chat_id, "✅ Nothing to do — all jobs already completed. "
                                  "Send /run fresh=yes to restart from scratch.")
            else:
                bot.send(chat_id, _completion_text(sargs, stats))
        except SystemExit as e:
            bot.send(chat_id, f"❌ Config error: {e}")
        except Exception as e:
            bot.send(chat_id, f"❌ Run failed: {_short_err(e)}")
        finally:
            kill_active_browsers()          # make absolutely sure browsers are gone
            with state.lock:
                state.running = False

    t = threading.Thread(target=worker, daemon=True)
    with state.lock:
        state.thread = t
    t.start()


def _handle_command(bot, cfg, state, uid, chat_id, text, frm):
    parts = text.split()
    cmd = parts[0].lower().split("@")[0]     # strip @botname
    args_tokens = parts[1:]

    # Always-allowed identity helpers (so users can self-configure).
    if cmd in ("/whoami", "/id"):
        name = frm.get("username") or frm.get("first_name") or ""
        bot.send(chat_id, f"Your Telegram user id: {uid}\n(username: @{name})")
        return

    authorized = uid in cfg.allowed_users
    if not authorized:
        bot.send(chat_id, f"⛔ Not authorized. Your id is {uid}. "
                          f"Ask an admin to run /adduser {uid}.")
        return

    if cmd in ("/start", "/help"):
        bot.send(chat_id, HELP_TEXT)
    elif cmd == "/run":
        _start_run(bot, cfg, state, chat_id, uid, args_tokens)
    elif cmd in ("/status", "/stats"):
        bot.send(chat_id, _status_text(state))
    elif cmd == "/stop":
        with state.lock:
            running = state.running
            runner = state.runner
        if not running:
            bot.send(chat_id, "Nothing is running.")
        else:
            if runner is not None:
                runner.stop_event.set()
            kill_active_browsers()
            bot.send(chat_id, "⏹ Stopping the current job and killing all browsers…")
    elif cmd == "/users":
        ids = ", ".join(str(u) for u in cfg.allowed_users) or "(none)"
        bot.send(chat_id, f"Authorized users: {ids}")
    elif cmd == "/adduser":
        if not args_tokens or not args_tokens[0].lstrip("-").isdigit():
            bot.send(chat_id, "Usage: /adduser <telegram_user_id>")
        else:
            new_id = int(args_tokens[0])
            if new_id in cfg.allowed_users:
                bot.send(chat_id, f"{new_id} is already authorized.")
            else:
                cfg.allowed_users.append(new_id)
                cfg.save()
                bot.send(chat_id, f"✅ Added {new_id}. Authorized users: "
                                  f"{', '.join(str(u) for u in cfg.allowed_users)}")
    elif cmd == "/removeuser":
        if not args_tokens or not args_tokens[0].lstrip("-").isdigit():
            bot.send(chat_id, "Usage: /removeuser <telegram_user_id>")
        else:
            rid = int(args_tokens[0])
            if rid in cfg.allowed_users:
                cfg.allowed_users.remove(rid)
                cfg.save()
                bot.send(chat_id, f"✅ Removed {rid}.")
            else:
                bot.send(chat_id, f"{rid} is not in the list.")
    else:
        bot.send(chat_id, "Unknown command. Send /help.")


def run_telegram_bot(args) -> int:
    cfg = load_bot_config(args)
    bot = TelegramBot(cfg.token)
    state = RunState()
    print(f"[bot] Telegram bot started. Authorized users: "
          f"{cfg.allowed_users or '(none — set allowed_users or use /adduser)'}",
          flush=True)
    print("[bot] Waiting for commands (Ctrl+C to quit)...", flush=True)

    # Optional periodic auto-updates to whoever started a run.
    def notifier():
        while not _SHUTDOWN.is_set():
            time.sleep(max(15, cfg.notify_interval))
            with state.lock:
                running = state.running
                chat = state.chat_id
                prog = state.progress
            if running and prog is not None and chat is not None:
                bot.send(chat, _status_text(state))
    if cfg.notify_interval > 0:
        threading.Thread(target=notifier, daemon=True).start()

    while not _SHUTDOWN.is_set():
        for u in bot.get_updates(timeout=25):
            msg = u.get("message") or u.get("edited_message")
            if not msg:
                continue
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            frm = msg.get("from", {}) or {}
            uid = frm.get("id")
            chat_id = (msg.get("chat", {}) or {}).get("id")
            if uid is None or chat_id is None:
                continue
            try:
                _handle_command(bot, cfg, state, uid, chat_id, text, frm)
            except Exception as e:
                bot.send(chat_id, f"⚠️ Error handling command: {_short_err(e)}")
    return 0


# ============================================================================
# Cluster: config, roles/permissions, first-run wizard, coordinator + workers
# ============================================================================

DEFAULT_CONFIG_PATH = "config.json"

DEFAULT_USER_PERMS = {
    "can_run": True, "can_stop": True, "can_configure": False,
    "can_upload_inputs": True, "max_threads": 4, "allowed_engines": "all",
}


def load_config(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise SystemExit(f"[fatal] could not read config {path}: {_short_err(e)}")


def save_config(path, cfg):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)


# -- roles / permissions ------------------------------------------------------

def _user_record(cfg, uid):
    return (cfg.get("users") or {}).get(str(uid))


def is_authorized(cfg, uid):
    return _user_record(cfg, uid) is not None


def is_admin(cfg, uid):
    r = _user_record(cfg, uid)
    return bool(r) and r.get("role") == "admin"


def has_perm(cfg, uid, perm):
    r = _user_record(cfg, uid)
    if not r:
        return False
    if r.get("role") == "admin":
        return True
    return bool((r.get("perms") or {}).get(perm, DEFAULT_USER_PERMS.get(perm)))


# ----------------------------------------------------------------------------
# Billing: subscription packages, USDT (TRC-20) + manual payments, enforcement
# ----------------------------------------------------------------------------

USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"   # official USDT on TRON


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_ts(s):
    """Parse an ISO timestamp (with optional trailing Z) to epoch seconds."""
    if not s:
        return 0.0
    try:
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def billing_cfg(cfg):
    return cfg.get("billing") or {}


def billing_enabled(cfg):
    return bool(billing_cfg(cfg).get("enabled"))


def usdt_cfg(cfg):
    u = billing_cfg(cfg).get("usdt") or {}
    return u if u.get("enabled") and u.get("wallet_address") else None


def manual_cfg(cfg):
    m = billing_cfg(cfg).get("manual") or {}
    return m if m.get("enabled") and (m.get("methods")) else None


def packages(cfg):
    return billing_cfg(cfg).get("packages") or []


def get_package(cfg, pid):
    for p in packages(cfg):
        if str(p.get("id")) == str(pid):
            return p
    return None


def upload_rules(cfg):
    u = billing_cfg(cfg).get("upload") or {}
    exts = [e.lower() for e in (u.get("allowed_extensions") or [".txt", ".csv"])]
    return exts, int(u.get("max_upload_mb", 5))


def get_subscription(cfg, uid):
    return (_user_record(cfg, uid) or {}).get("subscription")


def subscription_active(cfg, uid):
    """True if the user may run jobs. Admins always may; if billing is off,
    any authorized user may; otherwise a non-expired subscription is required."""
    if is_admin(cfg, uid):
        return True
    if not billing_enabled(cfg):
        return is_authorized(cfg, uid)
    s = get_subscription(cfg, uid)
    return bool(s) and _parse_iso_ts(s.get("expires")) > time.time()


def effective_threads(cfg, uid, requested):
    if is_admin(cfg, uid) or not billing_enabled(cfg):
        return requested
    s = get_subscription(cfg, uid) or {}
    cap = int(s.get("threads", 1))
    return max(1, min(int(requested), cap))


def effective_max_upload_mb(cfg, uid):
    if is_admin(cfg, uid):
        return 4096
    s = get_subscription(cfg, uid)
    if billing_enabled(cfg) and s and s.get("max_upload_mb"):
        return int(s["max_upload_mb"])
    _exts, g = upload_rules(cfg)
    return g


def can_purchase(cfg, uid, pkg):
    """Upgrade-only while subscribed: require strictly higher Package.tier."""
    s = get_subscription(cfg, uid)
    if s and _parse_iso_ts(s.get("expires")) > time.time():
        if int(pkg.get("tier", 1)) <= int(s.get("tier", 0)):
            return False, ("Upgrade-only while subscribed — pick a higher "
                           "tier package.")
    return True, ""


def activate_subscription(cfg, uid, pkg, path):
    users = cfg.setdefault("users", {})
    rec = users.setdefault(str(uid), {"role": "user", "perms": {}})
    if rec.get("role") != "admin":
        rec["role"] = "user"
    expires = datetime.now(timezone.utc) + timedelta(days=int(pkg.get("duration_days", 30)))
    rec["subscription"] = {
        "package": pkg.get("id"), "package_name": pkg.get("name", pkg.get("id")),
        "tier": int(pkg.get("tier", 1)), "threads": int(pkg.get("threads", 1)),
        "max_upload_mb": int(pkg.get("max_upload_mb", 5)),
        "expires": expires.isoformat(), "since": _now_iso(),
    }
    save_config(path, cfg)
    return rec["subscription"]


# -- USDT TRC-20 on-chain verification (TronScan free API) --------------------

def _txid_used(cfg, txid):
    return txid in (billing_cfg(cfg).get("used_txids") or [])


def _mark_txid_used(cfg, txid, path):
    b = cfg.setdefault("billing", {})
    lst = b.setdefault("used_txids", [])
    if txid not in lst:
        lst.append(txid)
        save_config(path, cfg)


def _parse_trc20_tx(data, txid, wallet, min_amount_usdt, contract):
    """Validate a TronScan transaction-info payload. Returns (ok, detail, amount)."""
    if not isinstance(data, dict) or not (data.get("hash") or data.get("contractData")):
        return False, "transaction not found on-chain (check the TxID / network)", 0.0
    ret = str(data.get("contractRet", "SUCCESS")).upper()
    if ret not in ("SUCCESS", ""):
        return False, f"transaction did not succeed on-chain ({ret})", 0.0
    confirmed = data.get("confirmed", True)
    transfers = data.get("trc20TransferInfo") or []
    if isinstance(transfers, dict):
        transfers = [transfers]
    for t in transfers:
        to = t.get("to_address") or t.get("to")
        ca = t.get("contract_address") or t.get("contractAddress")
        raw = t.get("amount_str") or t.get("quant") or t.get("amount") or "0"
        if to == wallet and (not contract or ca == contract):
            try:
                amount = int(raw) / 1_000_000.0    # USDT-TRC20 has 6 decimals
            except (TypeError, ValueError):
                amount = 0.0
            if amount + 1e-9 < float(min_amount_usdt):
                return False, (f"amount {amount:.2f} USDT is less than the required "
                               f"{float(min_amount_usdt):.2f} USDT"), amount
            if not confirmed:
                return False, "payment found but not yet confirmed — try /paid again shortly", amount
            return True, "verified", amount
    return False, "no matching USDT transfer to the receiving wallet in this transaction", 0.0


def verify_trc20_payment(txid, wallet, min_amount_usdt, ucfg):
    import requests
    contract = ucfg.get("contract") or USDT_TRC20_CONTRACT
    api_base = (ucfg.get("api_base") or "https://apilist.tronscanapi.com").rstrip("/")
    headers = {}
    if ucfg.get("api_key"):
        headers["TRON-PRO-API-KEY"] = ucfg["api_key"]
    try:
        r = requests.get(f"{api_base}/api/transaction-info",
                         params={"hash": txid}, headers=headers, timeout=30)
        data = r.json()
    except Exception as e:
        return False, f"could not query the blockchain ({_short_err(e)}); try again", 0.0
    return _parse_trc20_tx(data, txid, wallet, min_amount_usdt, contract)


# -- simple per-user rate limiter (anti-abuse) -------------------------------

class RateLimiter:
    def __init__(self, max_calls, per_seconds):
        self.max = max_calls
        self.per = per_seconds
        self.hits = {}
        self._lock = threading.Lock()

    def allow(self, key):
        now = time.time()
        with self._lock:
            q = [t for t in self.hits.get(key, []) if now - t < self.per]
            if len(q) >= self.max:
                self.hits[key] = q
                return False
            q.append(now)
            self.hits[key] = q
            return True


# -- first-run interactive setup wizard --------------------------------------

def run_setup_wizard(path, prompt=input) -> dict:
    print("=" * 62)
    print(" Google Maps Scraper — first-run setup")
    print("=" * 62)
    print("Choose how THIS machine should run:")
    print("  1) standalone         — just run jobs on this machine (no cluster)")
    print("  2) coordinator        — Telegram bot + dispatch to workers (no local scraping)")
    print("  3) coordinator+worker — Telegram bot AND scrape on this machine too")
    print("  4) worker             — scrape only; take work from a coordinator")
    choice = (prompt("Enter 1-4 [1]: ") or "1").strip()
    role = {"1": "standalone", "2": "coordinator", "3": "coordinator+worker",
            "4": "worker"}.get(choice, "standalone")

    cfg = {"role": role, "chunk_size": 500,
           "scrape": {"engine": "chrome", "threads": 2, "block_resources": "media",
                      "proxies": "proxies.txt", "locations": "locations.txt",
                      "keywords": "keywords.txt", "output_dir": "results"}}

    if role in ("coordinator", "coordinator+worker"):
        token = (prompt("Telegram bot token (from @BotFather): ") or "").strip()
        admin_id = (prompt("Your Telegram numeric user id (becomes ADMIN; "
                           "send /whoami to the bot to find it): ") or "").strip()
        port = (prompt("Port workers will connect to [8787]: ") or "8787").strip()
        gen = secrets.token_hex(16)
        secret = (prompt(f"Cluster shared secret [{gen}]: ") or gen).strip()
        cfg["telegram"] = {"token": token, "notify_interval_sec": 300}
        cfg["cluster"] = {"secret": secret, "bind_host": "0.0.0.0",
                          "bind_port": int(port or 8787), "lease_timeout_sec": 120}
        cfg["users"] = {}
        if admin_id.lstrip("-").isdigit():
            cfg["users"][admin_id] = {"role": "admin", "perms": {}}
        print(f"\nShare this with each worker machine:\n"
              f"  coordinator_url = http://THIS_HOST:{port}\n"
              f"  secret          = {secret}\n")
    elif role == "worker":
        url = (prompt("Coordinator URL (e.g. http://100.x.x.x:8787): ") or "").strip()
        secret = (prompt("Cluster shared secret (same as coordinator): ") or "").strip()
        name = (prompt(f"This worker's name [{socket.gethostname()}]: ")
                or socket.gethostname()).strip()
        cfg["cluster"] = {"coordinator_url": url.rstrip("/"), "secret": secret,
                          "worker_name": name, "lease_timeout_sec": 120}

    save_config(path, cfg)
    print(f"\nSaved config to {path}. Edit it any time, or re-run with --setup.")
    print("=" * 62)
    return cfg


def apply_config_to_args(args, cfg):
    """Overlay a config's scrape settings onto an args namespace."""
    for k, v in (cfg.get("scrape") or {}).items():
        key = str(k).replace("-", "_")
        if hasattr(args, key):
            setattr(args, key, _coerce(getattr(args, key), v))
    args.do_website_scrape = (args.scrape_websites == "yes") and not args.no_enrich
    if not args.output_dir:
        args.output_dir = "results"
    if "chunk_size" in cfg:
        args.chunk_size = int(cfg["chunk_size"])
    return args


# -- worker-side: run a specific global index range --------------------------

def execute_index_batch(args, keywords, locations, start, end, out_dir, ts,
                        stop_event, solver=None, log=print, on_progress=None) -> tuple[int, int]:
    """Run the jobs at global indices [start, end) (order: keyword outer,
    location inner) into out_dir as per-location CSVs. Returns (rows, failed).

    ``on_progress(done_in_chunk, rows)`` is called after each search and
    periodically (~2s) so panel agents can report live progress.
    """
    total = len(keywords) * len(locations)
    end = min(end, total)
    L = len(locations)
    jobs = [Job(keywords[i // L], locations[i % L]) for i in range(start, end)]
    if not jobs:
        return 0, 0

    if args.no_proxy or not os.path.exists(args.proxies):
        proxies = []
    else:
        proxies = load_proxies(args.proxies, require_auth=False)

    os.makedirs(out_dir, exist_ok=True)
    writer = PerLocationWriter(out_dir, ts)
    failed = FailedLog(os.path.join(out_dir, f"failed_{ts}.txt"))
    pacer = Pacer(args.min_delay, args.max_delay, args.cooldown_every,
                  args.cooldown_min, args.cooldown_max)
    runner = Runner(args, proxies, writer, pacer, solver=solver)
    runner.failed_log = failed
    runner.stop_event = stop_event

    def _emit(done_in_chunk: int, rows: int):
        if not on_progress:
            return
        try:
            on_progress(int(done_in_chunk), int(rows))
        except Exception:
            pass

    progress = ProgressTracker(
        len(jobs),
        rows_fn=lambda: runner.rows_written,
        on_tick=_emit,
    )
    runner.progress = progress

    stop_rep = threading.Event()
    reporter = None
    if on_progress:
        def _report_loop():
            while not stop_rep.wait(2.0):
                _emit(progress.done - progress.offset, runner.rows_written)
        reporter = threading.Thread(
            target=_report_loop, name="chunk-progress", daemon=True
        )
        reporter.start()

    pool = ThreadPoolExecutor(max_workers=args.threads)
    try:
        futures = [pool.submit(runner.run_job, j) for j in jobs]
        for _ in as_completed(futures):
            if _SHUTDOWN.is_set() or stop_event.is_set():
                break
    finally:
        stop_rep.set()
        if reporter is not None:
            reporter.join(timeout=1.0)
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            pool.shutdown(wait=False)
        try:
            kill_active_browsers()
        except Exception:
            pass
        writer.close()
        failed.close()
        _emit(progress.done - progress.offset, runner.rows_written)
    return runner.rows_written, failed.count


# -- ZIP helpers --------------------------------------------------------------

TELEGRAM_MAX_BYTES = 48 * 1024 * 1024   # keep under the 50 MB bot limit


def zip_files_split(file_list, out_prefix, max_bytes=TELEGRAM_MAX_BYTES):
    """Pack (path, arcname) pairs into as few ZIPs as possible, each roughly
    under max_bytes. Returns the list of zip paths created."""
    parts, batch, size, n = [], [], 0, 1

    def make(items, idx):
        p = f"{out_prefix}_part{idx}.zip"
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            for fp, arc in items:
                z.write(fp, arc)
        return p

    for fp, arc in file_list:
        try:
            fs = os.path.getsize(fp)
        except OSError:
            continue
        if batch and size + fs > max_bytes:
            parts.append(make(batch, n)); n += 1; batch = []; size = 0
        batch.append((fp, arc)); size += fs
    if batch:
        parts.append(make(batch, n))
    return parts


def _extract_zip(zip_path, dest):
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(dest)
    except Exception:
        pass


# -- coordinator: job + exactly-once chunk leasing ---------------------------

class JobManager:
    """One user's job, split into fixed-size index chunks. Each chunk is leased
    to exactly ONE worker (no duplicate keyword/location work). Dead workers'
    leases expire and are re-assigned."""

    def __init__(self, job_id, keywords, locations, settings, chunk_size,
                 requester_chat, requester_uid, job_dir, ts, lease_timeout=120):
        self.job_id = job_id
        self.keywords = keywords
        self.locations = locations
        self.settings = settings           # dict of scrape args for workers
        self.chunk_size = max(1, chunk_size)
        self.requester_chat = requester_chat
        self.requester_uid = requester_uid
        self.job_dir = job_dir
        self.ts = ts
        self.lease_timeout = lease_timeout
        self.total = len(keywords) * len(locations)
        self.cancelled = False
        self.rows = 0
        self.created = time.time()
        self._lock = threading.Lock()
        self.chunks = []
        cid = 0
        for s in range(0, self.total, self.chunk_size):
            self.chunks.append({"id": cid, "start": s,
                                "end": min(s + self.chunk_size, self.total),
                                "state": "pending", "worker": None, "ts": 0.0})
            cid += 1
        os.makedirs(os.path.join(job_dir, "parts"), exist_ok=True)

    def _reap(self):
        now = time.time()
        for c in self.chunks:
            if c["state"] == "leased" and now - c["ts"] > self.lease_timeout:
                c["state"] = "pending"; c["worker"] = None

    def next_chunk(self, worker_id):
        if self.cancelled:
            return None
        with self._lock:
            self._reap()
            for c in self.chunks:
                if c["state"] == "pending":
                    c["state"] = "leased"; c["worker"] = worker_id; c["ts"] = time.time()
                    return dict(c)
            return None

    def ack(self, worker_id, chunk_id, rows):
        with self._lock:
            for c in self.chunks:
                if c["id"] == chunk_id and c["state"] != "done":
                    c["state"] = "done"; self.rows += int(rows or 0)
                    return True
        return False

    def refresh(self, worker_id):
        with self._lock:
            now = time.time()
            for c in self.chunks:
                if c["state"] == "leased" and c["worker"] == worker_id:
                    c["ts"] = now

    def is_complete(self):
        with self._lock:
            return all(c["state"] == "done" for c in self.chunks)

    def snapshot(self):
        with self._lock:
            done = sum(c["end"] - c["start"] for c in self.chunks if c["state"] == "done")
            cd = sum(1 for c in self.chunks if c["state"] == "done")
            leased = sum(1 for c in self.chunks if c["state"] == "leased")
        return {"done": done, "total": self.total, "rows": self.rows,
                "chunks_done": cd, "chunks_total": len(self.chunks),
                "chunks_leased": leased,
                "pct": 100.0 * done / self.total if self.total else 100.0,
                "elapsed_s": time.time() - self.created}


def execute_run(args, state=None) -> dict:
    """Run one full scrape (streamed, chunked, resumable). Populates `state`
    (a RunState) for live monitoring, and respects both the global shutdown and
    the runner's per-run stop event. Returns a stats dict. Does NOT exit the
    process — so a Telegram bot can call it repeatedly."""
    print("Loading inputs...", flush=True)
    if args.no_proxy:
        proxies = []
        print("[load] proxies: none (direct connection)", flush=True)
    else:
        print(f"[load] reading proxies from {args.proxies} ...", flush=True)
        proxies = load_proxies(args.proxies, require_auth=False)
        print(f"[load] proxies loaded: {len(proxies)}", flush=True)

    print(f"[load] reading locations from {args.locations} ...", flush=True)
    locations = [format_location(l) for l in load_lines(args.locations)]
    print(f"[load] locations loaded: {len(locations)}", flush=True)

    print(f"[load] reading keywords from {args.keywords} ...", flush=True)
    keywords = load_lines(args.keywords)
    print(f"[load] keywords loaded: {len(keywords)}", flush=True)

    if not locations:
        raise SystemExit(f"[fatal] no locations in {args.locations}")
    if not keywords:
        raise SystemExit(f"[fatal] no keywords in {args.keywords}")

    # Total is computed by MULTIPLICATION — we never materialise the job list.
    total_jobs = len(keywords) * len(locations)
    print(f"[load] job space: {len(keywords)} keywords x {len(locations)} "
          f"locations = {total_jobs:,} jobs (streamed in chunks; not held in RAM)",
          flush=True)

    engine_bin = args.browser_path or (detect_brave_path() if args.engine == "brave" else None)
    print(f"Host OS       : {HOST_OS}")
    print(f"Engine        : {args.engine}" + (f" ({engine_bin})" if engine_bin else ""))
    print(f"Proxies       : {len(proxies)}")
    print(f"Keywords      : {len(keywords)}")
    print(f"Locations     : {len(locations)}")
    print(f"Total jobs    : {total_jobs:,} (keyword x location)")
    print(f"Threads       : {args.threads}")
    print(f"Chunk size    : {args.chunk_size}")
    print(f"Block resource: {args.block_resources}")
    print(f"Max/search    : {'unlimited (all)' if args.max_results <= 0 else args.max_results}")
    print(f"Scrape sites  : {args.do_website_scrape}")
    if args.engine != "camoufox" and args.threads > 3:
        print(f"[hint] {args.threads} threads = {args.threads} browsers "
              f"(~{args.threads * 400}MB+ RAM). Lower --threads if the machine "
              f"slows down or swaps.")
    print(f"Headless      : {args.headless}")
    print(f"Stealth       : {not args.no_stealth}")

    # --- session / resume (cursor-based) ----------------------------------
    print("[load] checking for a resumable session ...", flush=True)
    os.makedirs(args.output_dir, exist_ok=True)
    session_path = os.path.join(args.output_dir, SessionState.FILENAME)
    signature = SessionState.signature_for(args.engine, keywords, locations)
    fresh_ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    session, resumed = SessionState.load_or_new(
        session_path, signature, fresh_ts, total_jobs, resume=not args.fresh, log=print)
    if args.fresh:
        session.clear()
        session = SessionState(session_path, fresh_ts, signature, total_jobs)
        session._persist()
    run_ts = session.timestamp
    cursor = min(session.cursor, total_jobs)
    remaining = total_jobs - cursor

    print(f"Output dir    : {args.output_dir}  (one CSV per location)")
    print(f"File pattern  : <City_State_Country>_{run_ts}.csv")
    if resumed and cursor > 0:
        print(f"Session       : RESUMING at job {cursor:,}/{total_jobs:,} "
              f"({remaining:,} remaining).")
    else:
        print(f"Session       : new (checkpoint: {session_path})")
    print("-" * 60)

    if remaining <= 0:
        print("All jobs are already completed. Use --fresh to start over.")
        session.clear()
        return {"total_jobs": total_jobs, "processed": total_jobs, "rows": 0,
                "files": 0, "failed": 0, "elapsed_s": 0, "completed": True,
                "already_done": True}

    solver = None
    solvers = []
    if args.captcha_provider != "none":
        if not args.captcha_key:
            raise SystemExit("[fatal] --captcha-provider set but --captcha-key is missing.")
        solvers.append(CaptchaSolver(args.captcha_provider, args.captcha_key, args.captcha_host))
    backup_provider = getattr(args, "captcha_backup_provider", "none") or "none"
    backup_key = getattr(args, "captcha_backup_key", None)
    if backup_provider != "none":
        if not backup_key:
            print("[warn] captcha backup provider set but key missing — ignoring backup")
        else:
            solvers.append(
                CaptchaSolver(
                    backup_provider,
                    backup_key,
                    getattr(args, "captcha_backup_host", None),
                )
            )
    if solvers:
        solver = solvers[0] if len(solvers) == 1 else CaptchaSolverChain(solvers)
        solver.validate()   # fail fast on a bad key (or warn if backup exists)

    pacer = Pacer(args.min_delay, args.max_delay, args.cooldown_every,
                  args.cooldown_min, args.cooldown_max)
    writer = PerLocationWriter(args.output_dir, run_ts)
    failed_log = FailedLog(os.path.join(args.output_dir, f"failed_jobs_{run_ts}.txt"))
    runner = Runner(args, proxies, writer, pacer, solver=solver, session=session)
    runner.failed_log = failed_log
    stop_event = runner.stop_event

    # Progress reflects absolute position (starts at the resume cursor).
    prog_every = max(1, min(50, args.chunk_size))
    progress = ProgressTracker(total_jobs, rows_fn=lambda: runner.rows_written,
                               every=prog_every, start_done=cursor)
    runner.progress = progress

    # Expose live objects to the bot/monitor.
    if state is not None:
        state.attach(runner=runner, progress=progress, session=session,
                     writer=writer, failed_log=failed_log, total_jobs=total_jobs,
                     args=args, engine=args.engine, output_dir=args.output_dir)

    # Lazy, streamed job generator in deterministic order; skip up to the cursor.
    def all_jobs():
        for kw in keywords:
            for loc in locations:
                yield Job(kw, loc)
    job_iter = itertools.islice(all_jobs(), cursor, None)

    def stopping():
        return _SHUTDOWN.is_set() or stop_event.is_set()

    chunk_size = max(1, args.chunk_size)
    print(f"Starting scrape of {remaining:,} job(s) in chunks of {chunk_size} "
          f"on {args.threads} thread(s)...", flush=True)
    start = time.time()
    interrupted = False
    processed = cursor
    # Managed manually (not `with`) so shutdown doesn't block waiting on jobs.
    pool = ThreadPoolExecutor(max_workers=args.threads)
    try:
        # Only `chunk_size` jobs are ever queued at once, so memory/CPU stay flat
        # regardless of the total job count.
        while not stopping():
            chunk = list(itertools.islice(job_iter, chunk_size))
            if not chunk:
                break
            futures = [pool.submit(runner.run_job, j) for j in chunk]
            for _ in as_completed(futures):
                if stopping():
                    break
            if stopping():
                break
            processed += len(chunk)
            session.set_cursor(processed)     # checkpoint after each chunk
            print(f"[chunk] checkpoint {processed:,}/{total_jobs:,} jobs "
                  f"({100.0 * processed / total_jobs:.2f}%)", flush=True)
    except KeyboardInterrupt:
        interrupted = True
        print("\n[interrupted] stopping. Progress is saved — relaunch to resume.")
    finally:
        if stopping():
            interrupted = True
        kill_active_browsers()            # close every browser/child for this run
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            pool.shutdown(wait=False)     # Python < 3.9
        writer.close()
        failed_log.close()

    elapsed = time.time() - start
    files = writer.files()
    completed = processed >= total_jobs and not interrupted
    print("-" * 60)
    print(f"Rows written this run: {runner.rows_written} across "
          f"{len(files)} location file(s). Elapsed: {fmt_duration(elapsed)}.")
    print(f"Progress: {processed:,}/{total_jobs:,} jobs processed.")
    if failed_log.count:
        print(f"Failed/skipped jobs: {failed_log.count} (logged to {failed_log.path})")
    for loc, path in sorted(files.items()):
        print(f"  {loc}: {path}")

    if completed:
        session.clear()
        print("All jobs completed — session checkpoint cleared.")
    elif processed < total_jobs:
        print(f"{total_jobs - processed:,} job(s) remaining. Relaunch to resume "
              f"(or --fresh to restart).")

    return {"total_jobs": total_jobs, "processed": processed,
            "rows": runner.rows_written, "files": len(files),
            "failed": failed_log.count, "elapsed_s": elapsed,
            "completed": completed, "interrupted": interrupted,
            "output_dir": args.output_dir, "already_done": False}


def build_args_from_settings(settings: dict):
    a = parse_args([])
    for k, v in (settings or {}).items():
        key = str(k).replace("-", "_")
        if hasattr(a, key):
            setattr(a, key, _coerce(getattr(a, key), v))
    a.do_website_scrape = (a.scrape_websites == "yes") and not a.no_enrich
    if not a.output_dir:
        a.output_dir = "results"
    a.bot = False
    return a


class Coordinator:
    """Owns the Telegram bot, the worker registry, the job queue, and the
    exactly-once chunk scheduler. Distributes one job at a time across all
    connected workers; queues additional jobs."""

    def __init__(self, cfg, cfg_path):
        self.cfg = cfg
        self.cfg_path = cfg_path
        cl = cfg.get("cluster") or {}
        self.secret = cl.get("secret", "")
        self.lease_timeout = int(cl.get("lease_timeout_sec", 120))
        self.chunk_size = int(cfg.get("chunk_size", 500))
        tk = (cfg.get("telegram") or {}).get("token")
        self.bot = TelegramBot(tk) if tk else None
        self.lock = threading.Lock()
        self.workers = {}          # worker_id -> {name,last,cpu,mem}
        self.queue = []            # pending job specs
        self.active = None         # JobManager
        self.base_dir = cl.get("work_dir", "cluster_jobs")
        os.makedirs(os.path.join(self.base_dir, "inputs"), exist_ok=True)
        # billing / anti-abuse
        self.pending_orders = {}   # uid(str) -> {"package": id, "created": ts}
        self._pay_limiter = RateLimiter(5, 600)     # /paid: 5 per 10 min per user
        self._cmd_limiter = RateLimiter(30, 60)     # 30 commands / min per user

    # --- worker registry / RPC (called by HTTP handler + local worker) ------

    def register_worker(self, name):
        wid = secrets.token_hex(6)
        with self.lock:
            self.workers[wid] = {"name": name, "last": time.time(), "cpu": 0, "mem": 0}
        return wid

    def worker_heartbeat(self, wid, cpu, mem):
        with self.lock:
            w = self.workers.get(wid)
            if w:
                w.update(cpu=cpu, mem=mem, last=time.time())
            active = self.active
        if active:
            active.refresh(wid)
        return {"ok": True, "cancel": bool(active and active.cancelled)}

    def lease(self, wid):
        with self.lock:
            active = self.active
        if active is None:
            return {"chunk": None}
        c = active.next_chunk(wid)
        if c is None:
            return {"chunk": None}
        return {"chunk": c, "job": {"job_id": active.job_id, "ts": active.ts,
                                    "keywords": active.keywords,
                                    "locations": active.locations,
                                    "settings": active.settings}}

    def ack(self, wid, job_id, chunk_id, rows):
        with self.lock:
            active = self.active
        if active and active.job_id == job_id:
            active.ack(wid, chunk_id, rows)
        return {"ok": True}

    def save_part(self, job_id, chunk_id, zip_bytes):
        with self.lock:
            active = self.active
        if not active or active.job_id != job_id:
            return
        dest = os.path.join(active.job_dir, "parts", str(chunk_id))
        os.makedirs(dest, exist_ok=True)
        tmp = os.path.join(dest, "_part.zip")
        try:
            with open(tmp, "wb") as f:
                f.write(zip_bytes)
            _extract_zip(tmp, dest)
            os.remove(tmp)
        except Exception:
            pass

    # --- job lifecycle ------------------------------------------------------

    def submit_job(self, spec):
        with self.lock:
            self.queue.append(spec)
            self._activate_locked()
        return len(self.queue)

    def _activate_locked(self):
        if self.active is not None or not self.queue:
            return
        spec = self.queue.pop(0)
        jm = JobManager(spec["job_id"], spec["keywords"], spec["locations"],
                        spec["settings"], self.chunk_size, spec["chat"], spec["uid"],
                        os.path.join(self.base_dir, spec["job_id"]), spec["ts"],
                        self.lease_timeout)
        self.active = jm
        if self.bot:
            self.bot.send(spec["chat"],
                          f"▶️ Job started: {jm.total:,} searches in {len(jm.chunks)} "
                          f"chunks across {len(self.workers)} worker(s).")

    def cancel_active(self, uid):
        with self.lock:
            active = self.active
        if not active:
            return False
        if active.requester_uid != uid and not is_admin(self.cfg, uid):
            return False
        active.cancelled = True
        return True

    def scheduler_loop(self):
        while not _SHUTDOWN.is_set():
            time.sleep(3)
            now = time.time()
            with self.lock:
                for wid in list(self.workers):
                    if now - self.workers[wid]["last"] > self.lease_timeout * 2:
                        del self.workers[wid]
                active = self.active
            if active is None:
                with self.lock:
                    self._activate_locked()
                continue
            if active.cancelled or active.is_complete():
                self._finalize(active, cancelled=active.cancelled)
                with self.lock:
                    self.active = None
                    self._activate_locked()

    def _finalize(self, jm, cancelled):
        parts_dir = os.path.join(jm.job_dir, "parts")
        merge_dir = os.path.join(jm.job_dir, "merged")
        os.makedirs(merge_dir, exist_ok=True)
        w = PerLocationWriter(merge_dir, jm.ts)
        for root, _dirs, files in os.walk(parts_dir):
            for f in files:
                if not f.lower().endswith(".csv"):
                    continue
                try:
                    with open(os.path.join(root, f), newline="", encoding="utf-8") as fh:
                        for row in csv.DictReader(fh):
                            w.write(row.get("query_location") or "results", row)
                except Exception:
                    pass
        w.close()
        files = w.files()
        snap = jm.snapshot()
        head = "⏹ Job stopped (partial results)." if cancelled else "✅ Job complete!"
        if not self.bot:
            return
        if not files:
            self.bot.send(jm.requester_chat, f"{head}\nNo businesses collected.")
            return
        flist = [(p, os.path.basename(p)) for p in files.values()]
        zips = zip_files_split(flist, os.path.join(jm.job_dir, f"results_{jm.ts}"))
        self.bot.send(jm.requester_chat,
                      f"{head}\nSearches processed: {snap['done']:,}/{snap['total']:,}\n"
                      f"Businesses: {snap['rows']:,}\nSending {len(zips)} file(s)…")
        for z in zips:
            self.bot.send_document(jm.requester_chat, z, caption=os.path.basename(z))

    # --- built-in local worker (coordinator+worker) -------------------------

    def local_worker_loop(self):
        wid = self.register_worker(socket.gethostname() + " (local)")
        while not _SHUTDOWN.is_set():
            try:
                import psutil
                cpu = psutil.cpu_percent(interval=None)
                mem = psutil.virtual_memory().percent
            except Exception:
                cpu = mem = 0
            self.worker_heartbeat(wid, cpu, mem)
            lease = self.lease(wid)
            chunk = lease.get("chunk")
            if not chunk:
                time.sleep(2)
                continue
            job = lease["job"]
            with self.lock:
                active = self.active
            if active is None:
                continue
            se = threading.Event()
            watch = threading.Event()

            def _watch():
                while not watch.is_set() and not _SHUTDOWN.is_set():
                    with self.lock:
                        a = self.active
                    if a is None or a.cancelled or a.job_id != job["job_id"]:
                        se.set(); return
                    watch.wait(3)
            threading.Thread(target=_watch, daemon=True).start()

            out_dir = os.path.join(active.job_dir, "parts", str(chunk["id"]))
            sargs = build_args_from_settings(job["settings"])
            try:
                rows, _failed = execute_index_batch(
                    sargs, job["keywords"], job["locations"],
                    chunk["start"], chunk["end"], out_dir, job["ts"], se)
            except Exception as e:
                print(f"[local-worker] chunk error: {_short_err(e)}", flush=True)
                rows = 0
            watch.set()
            self.ack(wid, job["job_id"], chunk["id"], rows)

    # --- Telegram command handling ------------------------------------------

    def _input_path(self, uid, kind):
        p = os.path.join(self.base_dir, "inputs", f"{uid}_{kind}.txt")
        return p if os.path.exists(p) else None

    def _build_job_spec(self, uid, chat, tokens):
        kw_path = self._input_path(uid, "keywords") or \
            (self.cfg.get("scrape") or {}).get("keywords", "keywords.txt")
        loc_path = self._input_path(uid, "locations") or \
            (self.cfg.get("scrape") or {}).get("locations", "locations.txt")
        if not os.path.exists(kw_path):
            raise ValueError(f"no keywords file ({kw_path}). Upload a .txt with caption 'keywords'.")
        if not os.path.exists(loc_path):
            raise ValueError(f"no locations file ({loc_path}). Upload a .txt with caption 'locations'.")
        keywords = load_lines(kw_path)
        locations = [format_location(l) for l in load_lines(loc_path)]
        if not keywords or not locations:
            raise ValueError("keywords/locations are empty.")
        settings = dict(self.cfg.get("scrape") or {})
        for tok in tokens:
            if "=" in tok:
                k, v = tok.split("=", 1)
                settings[k.strip().replace("-", "_")] = v
        rec = _user_record(self.cfg, uid) or {}
        if rec.get("role") != "admin":
            perms = rec.get("perms") or {}
            # thread cap: min of permission cap and subscription cap
            perm_cap = int(perms.get("max_threads", DEFAULT_USER_PERMS["max_threads"]))
            cap = effective_threads(self.cfg, uid, perm_cap)
            if int(settings.get("threads", 2)) > cap:
                settings["threads"] = cap
            ae = perms.get("allowed_engines", "all")
            if ae != "all" and settings.get("engine", "chrome") not in ae:
                raise ValueError(f"engine '{settings.get('engine')}' not allowed for you.")
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        job_id = f"{uid}_{ts}_{secrets.token_hex(3)}"
        return {"job_id": job_id, "keywords": keywords, "locations": locations,
                "settings": settings, "chat": chat, "uid": uid, "ts": ts}

    def _status_text(self):
        with self.lock:
            active = self.active
            nworkers = len(self.workers)
            qlen = len(self.queue)
            wsummary = "; ".join(f"{w['name']} (cpu {w['cpu']:.0f}%, mem {w['mem']:.0f}%)"
                                 for w in self.workers.values()) or "none"
        if active is None:
            return f"⚪ Idle.\nWorkers: {nworkers} [{wsummary}]\nQueued jobs: {qlen}"
        s = active.snapshot()
        return (f"🟢 Running\n"
                f"Searches: {s['done']:,}/{s['total']:,} ({s['pct']:.1f}%)\n"
                f"Chunks: {s['chunks_done']}/{s['chunks_total']} done, {s['chunks_leased']} in progress\n"
                f"Businesses so far: {s['rows']:,}\n"
                f"Elapsed: {fmt_duration(s['elapsed_s'])}\n"
                f"Workers: {nworkers} [{wsummary}]\nQueued jobs: {qlen}")

    def handle_command(self, uid, chat, text, frm):
        parts = text.split()
        cmd = parts[0].lower().split("@")[0]
        toks = parts[1:]

        if cmd in ("/whoami", "/id"):
            self.bot.send(chat, f"Your Telegram user id: {uid}")
            return

        # basic anti-abuse rate limit for everyone
        if not self._cmd_limiter.allow(str(uid)):
            return

        # billing commands are open to anyone (so people can buy access first)
        if billing_enabled(self.cfg) and cmd in ("/packages", "/plans", "/buy",
                                                 "/paid", "/subscription", "/me", "/renew"):
            return self._handle_billing(uid, chat, cmd, toks, frm)

        if not is_authorized(self.cfg, uid):
            extra = ""
            if billing_enabled(self.cfg):
                extra = " Send /packages to buy access."
            self.bot.send(chat, f"⛔ Not authorized. Your id is {uid}.{extra}")
            return
        admin = is_admin(self.cfg, uid)

        if cmd in ("/start", "/help"):
            self.bot.send(chat, self._help_text(admin))
        elif cmd == "/run":
            if not has_perm(self.cfg, uid, "can_run"):
                self.bot.send(chat, "⛔ You don't have permission to run jobs.")
                return
            if not subscription_active(self.cfg, uid):
                self.bot.send(chat, "⛔ Your subscription has expired or is inactive. "
                                    "Send /packages to renew or buy.")
                return
            try:
                spec = self._build_job_spec(uid, chat, toks)
            except Exception as e:
                self.bot.send(chat, f"❌ {e}")
                return
            self.submit_job(spec)
            nk, nl = len(spec["keywords"]), len(spec["locations"])
            with self.lock:
                started = self.active is not None and self.active.job_id == spec["job_id"]
                qlen = len(self.queue)
            where = "started now" if started else f"queued (position {qlen})"
            self.bot.send(chat, f"✅ Job accepted: {nk} keywords × {nl} locations "
                                f"= {nk * nl:,} searches — {where}. Use /status.")
        elif cmd in ("/status", "/stats"):
            self.bot.send(chat, self._status_text())
        elif cmd == "/stop":
            if not has_perm(self.cfg, uid, "can_stop"):
                self.bot.send(chat, "⛔ You don't have permission to stop jobs.")
                return
            ok = self.cancel_active(uid)
            self.bot.send(chat, "⏹ Stopping current job; results so far will be sent."
                          if ok else "Nothing running that you can stop.")
        elif cmd == "/servers":
            if not admin:
                self.bot.send(chat, "⛔ Admins only.")
                return
            with self.lock:
                if not self.workers:
                    self.bot.send(chat, "No workers connected.")
                else:
                    lines = [f"• {w['name']}: cpu {w['cpu']:.0f}%, mem {w['mem']:.0f}%"
                             for w in self.workers.values()]
                    self.bot.send(chat, "Connected workers:\n" + "\n".join(lines))
        elif cmd == "/users":
            if not admin:
                self.bot.send(chat, "⛔ Admins only.")
                return
            us = self.cfg.get("users") or {}
            lines = [f"• {u}: {r.get('role','user')}" for u, r in us.items()]
            self.bot.send(chat, "Users:\n" + ("\n".join(lines) or "(none)"))
        elif cmd in ("/adduser", "/removeuser", "/setrole", "/setperm"):
            if not admin:
                self.bot.send(chat, "⛔ Admins only.")
                return
            self._admin_user_cmd(chat, cmd, toks)
        elif cmd == "/pending":
            if not admin:
                self.bot.send(chat, "⛔ Admins only.")
                return
            if not self.pending_orders:
                self.bot.send(chat, "No pending orders.")
            else:
                lines = [f"• {u} → package {o['package']}" for u, o in self.pending_orders.items()]
                self.bot.send(chat, "Pending orders (approve with /approve <id> <package>):\n"
                              + "\n".join(lines))
        elif cmd == "/approve":
            if not admin:
                self.bot.send(chat, "⛔ Admins only.")
                return
            if len(toks) < 2 or not toks[0].lstrip("-").isdigit():
                self.bot.send(chat, "Usage: /approve <user_id> <package_id>")
                return
            pkg = get_package(self.cfg, toks[1])
            if not pkg:
                self.bot.send(chat, f"No such package '{toks[1]}'. See config packages.")
                return
            sub = activate_subscription(self.cfg, toks[0], pkg, self.cfg_path)
            self.pending_orders.pop(toks[0], None)
            self.bot.send(chat, f"✅ Activated {pkg.get('name')} for {toks[0]} "
                                f"until {sub['expires'][:10]}.")
            self.bot.send(int(toks[0]), f"✅ Your {pkg.get('name')} subscription is active "
                                        f"until {sub['expires'][:10]}. Send /run to start.")
        else:
            self.bot.send(chat, "Unknown command. Send /help.")

    def _admin_user_cmd(self, chat, cmd, toks):
        users = self.cfg.setdefault("users", {})
        if not toks or not toks[0].lstrip("-").isdigit():
            self.bot.send(chat, f"Usage: {cmd} <user_id> ...")
            return
        tid = toks[0]
        if cmd == "/adduser":
            users.setdefault(tid, {"role": "user", "perms": {}})
            save_config(self.cfg_path, self.cfg)
            self.bot.send(chat, f"✅ Added {tid} as user. Use /setperm or /setrole.")
        elif cmd == "/removeuser":
            if users.pop(tid, None) is not None:
                save_config(self.cfg_path, self.cfg)
                self.bot.send(chat, f"✅ Removed {tid}.")
            else:
                self.bot.send(chat, f"{tid} not found.")
        elif cmd == "/setrole":
            role = (toks[1] if len(toks) > 1 else "").lower()
            if role not in ("admin", "user"):
                self.bot.send(chat, "Usage: /setrole <id> admin|user")
                return
            users.setdefault(tid, {"role": "user", "perms": {}})["role"] = role
            save_config(self.cfg_path, self.cfg)
            self.bot.send(chat, f"✅ {tid} is now {role}.")
        elif cmd == "/setperm":
            rec = users.setdefault(tid, {"role": "user", "perms": {}})
            perms = rec.setdefault("perms", {})
            changed = []
            for t in toks[1:]:
                if "=" in t:
                    k, v = t.split("=", 1)
                    perms[k] = _coerce(DEFAULT_USER_PERMS.get(k, v), v)
                    changed.append(f"{k}={perms[k]}")
            save_config(self.cfg_path, self.cfg)
            self.bot.send(chat, f"✅ Permissions for {tid}: {', '.join(changed) or '(none changed)'}")

    # --- billing / payments -------------------------------------------------

    def _packages_text(self):
        pkgs = packages(self.cfg)
        if not pkgs:
            return "No subscription packages are configured yet."
        lines = ["Available packages:"]
        for p in sorted(pkgs, key=lambda x: int(x.get("tier", 1))):
            lines.append(
                f"\n• {p.get('name', p.get('id'))}  (id: {p.get('id')})\n"
                f"   Price: {p.get('price_usdt', '?')} USDT | {p.get('duration_days', 30)} days\n"
                f"   Threads: {p.get('threads', 1)} | Max upload: {p.get('max_upload_mb', 5)} MB")
        methods = []
        if usdt_cfg(self.cfg):
            methods.append("USDT (TRC-20)")
        if manual_cfg(self.cfg):
            methods.append("manual (bank/other)")
        lines.append(f"\nPayment methods: {', '.join(methods) or 'none configured'}")
        lines.append("Buy with:  /buy <package_id>")
        return "\n".join(lines)

    def _handle_billing(self, uid, chat, cmd, toks, frm):
        if cmd in ("/packages", "/plans"):
            self.bot.send(chat, self._packages_text())
        elif cmd in ("/subscription", "/me"):
            s = get_subscription(self.cfg, uid)
            if is_admin(self.cfg, uid):
                self.bot.send(chat, "You are an admin (no subscription needed).")
            elif not s:
                self.bot.send(chat, "You have no subscription. Send /packages to buy.")
            else:
                exp = _parse_iso_ts(s.get("expires"))
                days = max(0, (exp - time.time()) / 86400)
                state = "active" if exp > time.time() else "EXPIRED"
                self.bot.send(chat, f"Package: {s.get('package_name', s.get('package'))}\n"
                                    f"Status: {state}\nExpires: {s.get('expires', '')[:10]} "
                                    f"({days:.1f} days left)\nThreads: {s.get('threads')} | "
                                    f"Upload: {s.get('max_upload_mb')} MB")
        elif cmd in ("/buy", "/renew"):
            if not toks:
                self.bot.send(chat, "Usage: /buy <package_id>\n\n" + self._packages_text())
                return
            pkg = get_package(self.cfg, toks[0])
            if not pkg:
                self.bot.send(chat, f"No package '{toks[0]}'. Send /packages.")
                return
            ok, why = can_purchase(self.cfg, uid, pkg)
            if not ok:
                self.bot.send(chat, f"⛔ {why}")
                return
            self.pending_orders[str(uid)] = {"package": pkg["id"], "created": time.time()}
            self._send_payment_instructions(chat, pkg)
        elif cmd == "/paid":
            self._handle_paid(uid, chat, toks)

    def _send_payment_instructions(self, chat, pkg):
        price = pkg.get("price_usdt", "?")
        msg = [f"Order: {pkg.get('name', pkg.get('id'))} — {price} USDT for "
               f"{pkg.get('duration_days', 30)} days."]
        u = usdt_cfg(self.cfg)
        if u:
            msg.append(f"\n💠 Pay with USDT (TRC-20):\n"
                       f"Send exactly {price} USDT (TRC-20) to:\n{u['wallet_address']}\n"
                       f"Then send:  /paid <your_transaction_id>")
        m = manual_cfg(self.cfg)
        if m:
            msg.append("\n🏦 Or pay manually:")
            for meth in m.get("methods", []):
                msg.append(f"— {meth.get('name')}: {meth.get('details')}")
            msg.append("After paying, message an admin with your proof; they will /approve you.")
        if not u and not m:
            msg.append("\n⚠️ No payment method is configured. Contact the admin.")
        self.bot.send(chat, "\n".join(msg))

    def _handle_paid(self, uid, chat, toks):
        u = usdt_cfg(self.cfg)
        if not u:
            self.bot.send(chat, "USDT payments are not enabled. Contact the admin.")
            return
        if not self._pay_limiter.allow(str(uid)):
            self.bot.send(chat, "Too many verification attempts. Please wait a few minutes.")
            return
        order = self.pending_orders.get(str(uid))
        if not order:
            self.bot.send(chat, "No pending order. Send /buy <package_id> first.")
            return
        if not toks:
            self.bot.send(chat, "Usage: /paid <transaction_id>")
            return
        txid = toks[0].strip()
        if len(txid) < 40 or not re.fullmatch(r"[0-9a-fA-F]+", txid):
            self.bot.send(chat, "That doesn't look like a valid TRON transaction id.")
            return
        if _txid_used(self.cfg, txid):
            self.bot.send(chat, "⛔ That transaction has already been used.")
            return
        pkg = get_package(self.cfg, order["package"])
        if not pkg:
            self.bot.send(chat, "Your ordered package no longer exists. Send /packages.")
            return
        self.bot.send(chat, "🔎 Verifying your payment on-chain… (a few seconds)")
        ok, detail, amount = verify_trc20_payment(txid, u["wallet_address"],
                                                  pkg.get("price_usdt", 0), u)
        if not ok:
            self.bot.send(chat, f"❌ Payment not verified: {detail}")
            return
        _mark_txid_used(self.cfg, txid, self.cfg_path)
        sub = activate_subscription(self.cfg, uid, pkg, self.cfg_path)
        self.pending_orders.pop(str(uid), None)
        self.bot.send(chat, f"✅ Payment verified ({amount:.2f} USDT). "
                            f"{pkg.get('name')} active until {sub['expires'][:10]}. "
                            f"Send /run to start.")

    def handle_document(self, uid, chat, doc, caption):
        if not is_authorized(self.cfg, uid):
            self.bot.send(chat, f"⛔ Not authorized. Your id is {uid}."
                          + (" Send /packages to buy access." if billing_enabled(self.cfg) else ""))
            return
        if not has_perm(self.cfg, uid, "can_upload_inputs"):
            self.bot.send(chat, "⛔ You can't upload inputs.")
            return
        # extension + size limits (per-subscription size cap)
        allowed_exts, _g = upload_rules(self.cfg)
        fname = (doc.get("file_name") or "")
        ext = os.path.splitext(fname)[1].lower()
        if allowed_exts and ext not in allowed_exts:
            self.bot.send(chat, f"⛔ File type {ext or '(none)'} not allowed. "
                                f"Allowed: {', '.join(allowed_exts)}")
            return
        size_mb = (doc.get("file_size") or 0) / (1024 * 1024)
        cap_mb = effective_max_upload_mb(self.cfg, uid)
        if size_mb > cap_mb:
            self.bot.send(chat, f"⛔ File is {size_mb:.1f} MB; your limit is {cap_mb} MB.")
            return
        name = (doc.get("file_name") or "").lower()
        cap = (caption or "").strip().lower()
        kind = None
        if "keyword" in cap or "keyword" in name:
            kind = "keywords"
        elif "location" in cap or "location" in name:
            kind = "locations"
        if kind is None:
            self.bot.send(chat, "Send the .txt with caption 'keywords' or 'locations'.")
            return
        fp = self.bot.get_file_path(doc.get("file_id"))
        dest = os.path.join(self.base_dir, "inputs", f"{uid}_{kind}.txt")
        if fp and self.bot.download_file(fp, dest):
            n = len(load_lines(dest))
            self.bot.send(chat, f"✅ Saved {n} {kind}. Use /run when ready.")
        else:
            self.bot.send(chat, "❌ Could not download the file.")

    def _help_text(self, admin):
        base = ("Commands:\n"
                "/run [key=value …] — start a job (e.g. /run engine=brave threads=4)\n"
                "/status — live progress + workers\n"
                "/stop — stop your running job (partial results are sent)\n"
                "Upload a .txt with caption 'keywords' or 'locations' to set inputs.\n"
                "/whoami — your id\n/help — this message")
        if billing_enabled(self.cfg):
            base += ("\n\nSubscription:\n/packages — see plans & prices\n"
                     "/buy <package_id> — order a plan\n/paid <txid> — verify a USDT payment\n"
                     "/subscription — your plan & expiry")
        if admin:
            base += ("\n\nAdmin:\n/servers — connected workers + load\n"
                     "/users — list users\n/adduser <id> — add a user\n"
                     "/removeuser <id> — remove\n/setrole <id> admin|user\n"
                     "/setperm <id> can_run=1 max_threads=4 …\n"
                     "/pending — pending orders\n/approve <id> <package> — grant a plan")
        return base


def _start_coordinator_http(coord, host, port):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            return self.rfile.read(n) if n else b""

        def do_GET(self):
            if self.path.startswith("/ping"):
                return self._json(200, {"ok": True})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            # constant-time comparison to avoid timing attacks on the secret
            if not hmac.compare_digest(str(self.headers.get("X-Cluster-Secret", "")),
                                      str(coord.secret)):
                return self._json(403, {"error": "unauthorized"})
            raw = self._read()
            path = self.path.split("?")[0]
            try:
                if path == "/register":
                    d = json.loads(raw or b"{}")
                    return self._json(200, {"worker_id": coord.register_worker(d.get("name", "worker"))})
                if path == "/heartbeat":
                    d = json.loads(raw or b"{}")
                    return self._json(200, coord.worker_heartbeat(d.get("worker_id"), d.get("cpu", 0), d.get("mem", 0)))
                if path == "/lease":
                    d = json.loads(raw or b"{}")
                    return self._json(200, coord.lease(d.get("worker_id")))
                if path == "/ack":
                    d = json.loads(raw or b"{}")
                    return self._json(200, coord.ack(d.get("worker_id"), d.get("job_id"), d.get("chunk_id"), d.get("rows", 0)))
                if path == "/upload":
                    from urllib.parse import parse_qs, urlparse
                    q = parse_qs(urlparse(self.path).query)
                    coord.save_part(q.get("job_id", [""])[0], int(q.get("chunk_id", ["0"])[0]), raw)
                    return self._json(200, {"ok": True})
            except Exception as e:
                return self._json(500, {"error": _short_err(e)})
            self._json(404, {"error": "not found"})

    srv = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[coordinator] worker API listening on {host}:{port}", flush=True)
    return srv


def run_coordinator(cfg, cfg_path) -> int:
    coord = Coordinator(cfg, cfg_path)
    if not coord.bot:
        raise SystemExit("[fatal] coordinator needs a Telegram token in the config "
                         "(telegram.token). Re-run with --setup.")
    cl = cfg.get("cluster") or {}
    _start_coordinator_http(coord, cl.get("bind_host", "0.0.0.0"),
                            int(cl.get("bind_port", 8787)))
    threading.Thread(target=coord.scheduler_loop, daemon=True).start()
    if cfg.get("role") == "coordinator+worker":
        threading.Thread(target=coord.local_worker_loop, daemon=True).start()
        print("[coordinator] local worker enabled (this machine also scrapes).", flush=True)

    interval = int((cfg.get("telegram") or {}).get("notify_interval_sec", 300))

    def notifier():
        while not _SHUTDOWN.is_set():
            time.sleep(max(15, interval))
            with coord.lock:
                active = coord.active
            if interval > 0 and active is not None:
                coord.bot.send(active.requester_chat, coord._status_text())
    if interval > 0:
        threading.Thread(target=notifier, daemon=True).start()

    print(f"[coordinator] Telegram bot running. Users: "
          f"{list((cfg.get('users') or {}).keys()) or '(none — /adduser)'}", flush=True)
    while not _SHUTDOWN.is_set():
        for u in coord.bot.get_updates(timeout=25):
            msg = u.get("message") or u.get("edited_message")
            if not msg:
                continue
            frm = msg.get("from", {}) or {}
            uid = frm.get("id")
            chat = (msg.get("chat", {}) or {}).get("id")
            if uid is None or chat is None:
                continue
            try:
                if msg.get("document"):
                    coord.handle_document(uid, chat, msg["document"], msg.get("caption", ""))
                elif (msg.get("text") or "").strip():
                    coord.handle_command(uid, chat, msg["text"].strip(), frm)
            except Exception as e:
                coord.bot.send(chat, f"⚠️ Error: {_short_err(e)}")
    return 0


def run_worker(cfg) -> int:
    import requests
    cl = cfg.get("cluster") or {}
    base = (cl.get("coordinator_url") or "").rstrip("/")
    secret = cl.get("secret", "")
    name = cl.get("worker_name") or socket.gethostname()
    if not base:
        raise SystemExit("[fatal] worker needs cluster.coordinator_url in config. Re-run --setup.")
    hdr = {"X-Cluster-Secret": secret}
    print(f"[worker] {name} connecting to {base} …", flush=True)

    wid = None
    while not _SHUTDOWN.is_set() and wid is None:
        try:
            wid = requests.post(base + "/register", json={"name": name},
                                headers=hdr, timeout=20).json().get("worker_id")
        except Exception as e:
            print(f"[worker] register failed ({_short_err(e)}); retry in 5s", flush=True)
            time.sleep(5)
    print(f"[worker] registered as {wid}. Waiting for work…", flush=True)

    while not _SHUTDOWN.is_set():
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory().percent
        except Exception:
            cpu = mem = 0
        try:
            requests.post(base + "/heartbeat", json={"worker_id": wid, "cpu": cpu, "mem": mem},
                          headers=hdr, timeout=20)
        except Exception:
            pass
        try:
            lease = requests.post(base + "/lease", json={"worker_id": wid},
                                  headers=hdr, timeout=30).json()
        except Exception:
            time.sleep(3)
            continue
        chunk = lease.get("chunk")
        if not chunk:
            time.sleep(2)
            continue
        job = lease["job"]
        se = threading.Event()
        watch = threading.Event()

        def _hb():
            while not watch.is_set() and not _SHUTDOWN.is_set():
                try:
                    rr = requests.post(base + "/heartbeat",
                                       json={"worker_id": wid, "cpu": 0, "mem": 0},
                                       headers=hdr, timeout=20).json()
                    if rr.get("cancel"):
                        se.set()
                except Exception:
                    pass
                watch.wait(5)
        threading.Thread(target=_hb, daemon=True).start()

        out_dir = os.path.join("worker_out", job["job_id"], str(chunk["id"]))
        sargs = build_args_from_settings(job["settings"])
        try:
            rows, _f = execute_index_batch(sargs, job["keywords"], job["locations"],
                                           chunk["start"], chunk["end"], out_dir, job["ts"], se)
        except Exception as e:
            print(f"[worker] chunk error: {_short_err(e)}", flush=True)
            rows = 0
        watch.set()

        flist = []
        for root, _d, fs in os.walk(out_dir):
            for f in fs:
                if f.lower().endswith(".csv"):
                    flist.append((os.path.join(root, f), f))
        if flist:
            zp = os.path.join("worker_out", job["job_id"], f"chunk_{chunk['id']}.zip")
            try:
                with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
                    for fp, arc in flist:
                        z.write(fp, arc)
                with open(zp, "rb") as fh:
                    requests.post(base + f"/upload?job_id={job['job_id']}&chunk_id={chunk['id']}",
                                  data=fh.read(), headers=hdr, timeout=600)
            except Exception as e:
                print(f"[worker] upload failed: {_short_err(e)}", flush=True)
        try:
            requests.post(base + "/ack",
                          json={"worker_id": wid, "job_id": job["job_id"],
                                "chunk_id": chunk["id"], "rows": rows},
                          headers=hdr, timeout=20)
        except Exception:
            pass
        shutil.rmtree(os.path.join("worker_out", job["job_id"]), ignore_errors=True)
    return 0


def main(argv=None):
    args = parse_args(argv)

    # Trap Ctrl+C / SIGTERM / terminal-close so we kill all browsers + children.
    install_shutdown_handlers()

    # One-shot utilities work in any mode and don't need config.
    if args.selftest:
        ensure_dependencies(args)
        return run_selftest(args)
    if args.check_proxies:
        return run_check_proxies(args)
    if args.diagnose:
        ensure_dependencies(args)
        return run_diagnose(args)

    # ---- config / first-run setup ----------------------------------------
    cfg_path = args.config or DEFAULT_CONFIG_PATH
    cfg = load_config(cfg_path)

    # Did the user pass any CLI flags? (bare launch = they want the wizard)
    passed = list(argv) if argv is not None else sys.argv[1:]
    explicit = len(passed) > 0

    # First run (no config) with a bare launch, OR forced --setup: ask the user
    # to choose a mode and configure it. With a config present, we just run it.
    if args.setup or (cfg is None and not explicit):
        cfg = run_setup_wizard(cfg_path)

    # Role precedence: --role flag > config > 'standalone'.
    role = args.role or (cfg.get("role") if cfg else None) or "standalone"
    if args.bot and role == "standalone":
        role = "coordinator+worker"   # legacy --bot maps to single-node coordinator

    # ---- dispatch by role -------------------------------------------------
    if role in ("coordinator", "coordinator+worker"):
        if cfg is None:
            cfg = {"role": role, "telegram": {"token": args.telegram_token},
                   "cluster": {"secret": secrets.token_hex(16), "bind_host": "0.0.0.0",
                               "bind_port": 8787, "lease_timeout_sec": 120},
                   "users": {}, "chunk_size": args.chunk_size,
                   "scrape": {}}
            if args.telegram_users:
                for u in re.split(r"[,\s]+", args.telegram_users):
                    if u.strip().lstrip("-").isdigit():
                        cfg["users"][u.strip()] = {"role": "admin", "perms": {}}
        cfg["role"] = role
        return run_coordinator(cfg, cfg_path)

    if role == "worker":
        if cfg is None:
            raise SystemExit("[fatal] worker role needs a config (coordinator_url + "
                             "secret). Run with --setup.")
        return run_worker(cfg)

    # ---- standalone -------------------------------------------------------
    if cfg is not None and cfg.get("scrape") and not explicit:
        apply_config_to_args(args, cfg)
    ensure_dependencies(args)
    execute_run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
