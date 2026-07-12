#!/usr/bin/env python3
"""
Scrapeboard worker agent — Windows / macOS / Linux.

First run (no config, no flags) opens an interactive setup wizard that saves
worker_config.json. Browser engines and Python packages auto-install on first
use (same bootstrap as the original scraper).

Usage:
  python agent.py                         # first run → wizard; later → config
  python agent.py --setup                 # re-run wizard
  python agent.py --panel-url URL --token TOKEN
  python agent.py --selftest              # verify browser stack (no panel)
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VERSION = "0.3.0"
CONFIG_NAME = "worker_config.json"
HOST_OS = platform.system()  # Windows | Darwin | Linux


# ---------------------------------------------------------------------------
# Bootstrap: ensure agent deps exist before we import them
# ---------------------------------------------------------------------------

def _pip_install(pkgs: list[str]) -> None:
    print(f"[setup] installing: {', '.join(pkgs)}", flush=True)
    cmd = [sys.executable, "-m", "pip", "install", *pkgs]
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError:
        subprocess.check_call(cmd + ["--user"])


def bootstrap_agent_deps() -> None:
    """Install requests (required) and psutil (optional) if missing."""
    import importlib.util

    if importlib.util.find_spec("requests") is None:
        try:
            _pip_install(["requests"])
        except Exception as e:
            raise SystemExit(
                f"[fatal] could not install requests: {e}\n"
                f"Run:  {sys.executable} -m pip install -r requirements.txt"
            ) from e
    if importlib.util.find_spec("psutil") is None:
        try:
            _pip_install(["psutil"])
        except Exception:
            print(
                "[setup] psutil not installed (optional) — CPU/RAM stats and "
                "shutdown cleanup will be limited.",
                flush=True,
            )


def config_path() -> Path:
    return ROOT / CONFIG_NAME


def load_config() -> dict | None:
    p = config_path()
    if not p.exists():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise SystemExit(f"[fatal] could not read {p}: {e}") from e


def save_config(cfg: dict) -> None:
    p = config_path()
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, p)
    print(f"[setup] saved {p}", flush=True)


def run_setup_wizard(prompt=input) -> dict:
    print("=" * 62, flush=True)
    print(" Scrapeboard Worker — first-run setup", flush=True)
    print(f" OS: {HOST_OS} ({platform.machine()})  Python: {sys.version.split()[0]}", flush=True)
    print("=" * 62, flush=True)
    print("This machine will ONLY scrape. Config/users/billing live on the panel.", flush=True)
    print(flush=True)

    default_url = "https://scrape.cvmso.com"
    panel_url = (prompt(f"Panel URL [{default_url}]: ") or default_url).strip().rstrip("/")
    token = (prompt("Worker token (from Scrapeboard → Admin → Workers): ") or "").strip()
    if not token:
        raise SystemExit("[fatal] worker token is required. Create a worker in the panel first.")

    default_name = platform.node() or "worker"
    name = (prompt(f"Worker name [{default_name}]: ") or default_name).strip()
    engine = (prompt("Default browser engine for local selftest [chrome]: ") or "chrome").strip().lower()
    if engine not in ("chrome", "google-chrome", "edge", "brave", "camoufox"):
        engine = "chrome"

    work = (prompt("Work directory [auto temp]: ") or "").strip()

    cfg = {
        "panel_url": panel_url,
        "token": token,
        "worker_name": name,
        "default_engine": engine,
        "work_dir": work,
        "skip_setup": False,
    }
    save_config(cfg)

    print(flush=True)
    print("Next: dependencies + browser will auto-install on first job / --selftest.", flush=True)
    print(f"Start with:  {sys.executable} agent.py", flush=True)
    print("=" * 62, flush=True)
    return cfg


def _cpu_mem():
    try:
        import psutil
        return psutil.cpu_percent(interval=None), psutil.virtual_memory().percent
    except Exception:
        return 0.0, 0.0


class PanelClient:
    def __init__(self, base: str, token: str, worker_name: str = ""):
        import requests

        self.requests = requests
        self.base = base.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.worker_name = worker_name

    def heartbeat(self):
        cpu, mem = _cpu_mem()
        r = self.requests.post(
            f"{self.base}/api/worker-api/heartbeat",
            json={"cpu": cpu, "mem": mem, "version": VERSION, "name": self.worker_name, "os": HOST_OS},
            headers=self.headers,
            timeout=20,
        )
        r.raise_for_status()
        return r.json()

    def lease(self):
        r = self.requests.post(f"{self.base}/api/worker-api/lease", headers=self.headers, timeout=30)
        r.raise_for_status()
        return r.json()

    def upload(self, job_id: str, chunk_id: int, zip_path: Path):
        with open(zip_path, "rb") as fh:
            r = self.requests.post(
                f"{self.base}/api/worker-api/upload",
                params={"job_id": job_id, "chunk_id": chunk_id},
                headers=self.headers,
                files={"file": (zip_path.name, fh, "application/zip")},
                timeout=300,
            )
        r.raise_for_status()
        return r.json()

    def ack(self, job_id: str, chunk_id: int, rows: int):
        r = self.requests.post(
            f"{self.base}/api/worker-api/ack",
            json={"job_id": job_id, "chunk_id": chunk_id, "rows": rows},
            headers=self.headers,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()


def _zip_dir(src: Path, dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in src.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(src).as_posix())
    return dest


_SETUP_DONE_ENGINES: set[str] = set()


def ensure_engine_ready(settings: dict, force: bool = False, skip: bool = False) -> None:
    """Run the scraper's cross-platform first-run browser/package bootstrap."""
    import gmaps_scraper as gs

    args = gs.build_args_from_settings(settings)
    engine = getattr(args, "engine", "chrome") or "chrome"
    if skip:
        args.skip_setup = True
    if force:
        args.force_setup = True
    if not force and engine in _SETUP_DONE_ENGINES:
        return
    print(f"[setup] ensuring deps + browser for engine={engine} on {HOST_OS}…", flush=True)
    gs.ensure_dependencies(args)
    _SETUP_DONE_ENGINES.add(engine)


def run_chunk(job: dict, chunk: dict, work_dir: Path, skip_setup: bool = False) -> tuple[int, Path | None]:
    import gmaps_scraper as gs

    keywords = job.get("keywords") or []
    locations = job.get("locations") or []
    settings = dict(job.get("settings") or {})
    proxies_text = job.get("proxies_text") or ""

    ensure_engine_ready(settings, skip=skip_setup)

    work_dir.mkdir(parents=True, exist_ok=True)
    proxies_path = work_dir / "proxies.txt"
    proxies_path.write_text(proxies_text, encoding="utf-8")

    args = gs.build_args_from_settings(settings)
    args.proxies = str(proxies_path)
    args.no_proxy = not bool(proxies_text.strip())
    args.threads = max(1, int(settings.get("threads") or 1))
    args.skip_setup = True  # already ensured above

    out_dir = work_dir / "out" / str(chunk["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    ts = str(job.get("ts") or "run")

    rows, _failed = gs.execute_index_batch(
        args,
        keywords,
        [gs.format_location(l) for l in locations],
        int(chunk["start"]),
        int(chunk["end"]),
        str(out_dir),
        ts,
        stop,
    )
    zip_path = work_dir / f"chunk_{chunk['id']}.zip"
    if any(out_dir.rglob("*.csv")):
        _zip_dir(out_dir, zip_path)
        return int(rows or 0), zip_path
    return int(rows or 0), None


def run_selftest(engine: str, force_setup: bool = False) -> int:
    import gmaps_scraper as gs

    args = gs.parse_args([])
    args.engine = engine
    args.selftest = True
    if force_setup:
        args.force_setup = True
    gs.install_shutdown_handlers()
    gs.ensure_dependencies(args)
    return gs.run_selftest(args)


def parse_cli(argv=None):
    p = argparse.ArgumentParser(
        description="Scrapeboard worker agent (Windows / macOS / Linux)."
    )
    p.add_argument("--panel-url", default="", help="Control panel base URL")
    p.add_argument("--token", default="", help="Worker token from the panel")
    p.add_argument("--name", default="", help="Worker display name")
    p.add_argument("--work-dir", default="", help="Scratch directory for chunk work")
    p.add_argument("--config", default="", help=f"Config path (default: {CONFIG_NAME})")
    p.add_argument("--setup", action="store_true", help="Run first-run wizard and exit/continue")
    p.add_argument("--selftest", action="store_true", help="Verify browser/stealth locally, then exit")
    p.add_argument("--engine", default="", help="Engine for --selftest / first bootstrap")
    p.add_argument("--skip-setup", action="store_true", help="Do not auto-install browsers/deps")
    p.add_argument("--force-setup", action="store_true", help="Re-run browser/deps install")
    return p.parse_args(argv)


def resolve_runtime(args) -> dict:
    """Merge CLI + config; run wizard when needed."""
    global CONFIG_NAME
    if args.config:
        cfg_path = Path(args.config)
    else:
        cfg_path = config_path()

    cfg = None
    if cfg_path.exists():
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            raise SystemExit(f"[fatal] could not read {cfg_path}: {e}") from e

    has_cli_creds = bool(args.panel_url.strip() and args.token.strip())

    if args.setup or (not has_cli_creds and cfg is None):
        # Wizard always writes default worker_config.json in ROOT
        cfg = run_setup_wizard()

    if cfg is None:
        cfg = {}

    panel_url = (args.panel_url or cfg.get("panel_url") or "").strip().rstrip("/")
    token = (args.token or cfg.get("token") or "").strip()
    if not panel_url or not token:
        raise SystemExit(
            "[fatal] panel URL and worker token required.\n"
            f"  Run:  {sys.executable} agent.py --setup\n"
            "  Or:   python agent.py --panel-url https://scrape.cvmso.com --token TOKEN"
        )

    return {
        "panel_url": panel_url,
        "token": token,
        "worker_name": args.name or cfg.get("worker_name") or platform.node() or "worker",
        "work_dir": args.work_dir or cfg.get("work_dir") or "",
        "default_engine": args.engine or cfg.get("default_engine") or "chrome",
        "skip_setup": bool(args.skip_setup or cfg.get("skip_setup")),
        "force_setup": bool(args.force_setup),
    }


def main(argv=None) -> int:
    # Parse first so --help does not trigger pip installs
    args = parse_cli(argv)
    bootstrap_agent_deps()

    # Local selftest does not need panel credentials
    if args.selftest:
        engine = args.engine or "chrome"
        if not args.engine:
            cfg = load_config() or {}
            engine = cfg.get("default_engine") or "chrome"
        print(f"[worker] selftest engine={engine} os={HOST_OS}", flush=True)
        return run_selftest(engine, force_setup=args.force_setup)

    # If --setup alone and config will be written, resolve_runtime runs wizard
    rt = resolve_runtime(args)

    import gmaps_scraper as gs

    gs.install_shutdown_handlers()

    # Eager first-run browser setup for default engine (so first job is faster)
    if not rt["skip_setup"]:
        try:
            ensure_engine_ready(
                {"engine": rt["default_engine"]},
                force=rt["force_setup"],
                skip=False,
            )
        except SystemExit as e:
            print(e, flush=True)
            return 1

    client = PanelClient(rt["panel_url"], rt["token"], rt["worker_name"])
    if rt["work_dir"]:
        work_root = Path(rt["work_dir"])
    else:
        work_root = Path(tempfile.mkdtemp(prefix="scrapeboard_worker_"))
    work_root.mkdir(parents=True, exist_ok=True)

    print(
        f"[worker] v{VERSION} os={HOST_OS} name={rt['worker_name']}\n"
        f"[worker] panel={rt['panel_url']}  work={work_root}",
        flush=True,
    )

    while True:
        try:
            hb = client.heartbeat()
            if not hb.get("enabled", True):
                print("[worker] disabled by panel; sleeping", flush=True)
                time.sleep(10)
                continue
            if hb.get("drain"):
                print("[worker] draining; no new leases", flush=True)
                time.sleep(5)
                continue
            lease = client.lease()
            chunk = lease.get("chunk")
            if not chunk:
                time.sleep(2)
                continue
            job = lease["job"]
            print(
                f"[worker] leased job={job['job_id']} chunk={chunk['id']} "
                f"[{chunk['start']}:{chunk['end']}]",
                flush=True,
            )
            rows = 0
            try:
                rows, zip_path = run_chunk(
                    job,
                    chunk,
                    work_root / job["job_id"],
                    skip_setup=rt["skip_setup"],
                )
                if zip_path and zip_path.exists():
                    client.upload(job["job_id"], chunk["id"], zip_path)
                    print(f"[worker] uploaded chunk={chunk['id']}", flush=True)
            except Exception as e:
                print(f"[worker] chunk error: {e}", flush=True)
                rows = 0
            client.ack(job["job_id"], chunk["id"], rows)
            print(f"[worker] ack chunk={chunk['id']} rows={rows}", flush=True)
        except KeyboardInterrupt:
            print("[worker] stop", flush=True)
            try:
                gs.shutdown_all("worker interrupt")
            except Exception:
                pass
            return 0
        except Exception as e:
            print(f"[worker] loop error: {e}; retry in 5s", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
