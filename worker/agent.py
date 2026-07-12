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
  python agent.py --service               # background service (log + stable work dir)
  bash install_service.sh                 # macOS/Linux: install at login
  install_service.bat                     # Windows: Scheduled Task at logon
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

VERSION = "0.8.0"
CONFIG_NAME = "worker_config.json"
HOST_OS = platform.system()  # Windows | Darwin | Linux
SERVICE_NAME = "scrapeboard-worker"
REPO_ROOT = ROOT.parent  # checkout root (worker/ lives one level down)


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


def _prompt_yes_no(prompt_fn, question: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = (prompt_fn(f"{question} [{hint}]: ") or "").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "1", "true")


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "y", "on")


def _assume_yes() -> bool:
    return _env_truthy("SCRAPEBOARD_ASSUME_YES")


def run_setup_wizard(prompt=input) -> dict:
    print("=" * 62, flush=True)
    print(" Scrapeboard Worker — first-run setup", flush=True)
    print(f" OS: {HOST_OS} ({platform.machine()})  Python: {sys.version.split()[0]}", flush=True)
    print("=" * 62, flush=True)
    print("This machine will ONLY scrape. Config/users/billing live on the panel.", flush=True)
    print(flush=True)

    assume = _assume_yes()
    default_url = "https://scrape.cvmso.com"
    env_url = (os.environ.get("SCRAPEBOARD_PANEL_URL") or "").strip().rstrip("/")
    env_token = (os.environ.get("SCRAPEBOARD_TOKEN") or "").strip()
    env_name = (os.environ.get("SCRAPEBOARD_WORKER_NAME") or "").strip()
    # Tailscale: only when already requested via env/flag — never force interactive login.
    want_ts = _env_truthy("SCRAPEBOARD_TAILSCALE")

    if assume:
        print("[setup] Noninteractive mode (SCRAPEBOARD_ASSUME_YES=1).", flush=True)
        panel_url = env_url or default_url
        token = env_token
        if not token:
            raise SystemExit(
                "[fatal] Noninteractive setup needs a worker token.\n"
                "  Export SCRAPEBOARD_PANEL_URL and SCRAPEBOARD_TOKEN, or create\n"
                "  worker_config.json first, then re-run with --yes.\n"
                "  Panel → Admin → Workers → Create → copy token once."
            )
        name = env_name or platform.node() or "worker"
        engine = (os.environ.get("SCRAPEBOARD_ENGINE") or "chrome").strip().lower()
        work = (os.environ.get("SCRAPEBOARD_WORK_DIR") or "").strip()
        print(f"  panel_url={panel_url}", flush=True)
        print(f"  worker_name={name}", flush=True)
        print(f"  engine={engine}  tailscale={want_ts}", flush=True)
    else:
        url_default = env_url or default_url
        panel_url = (prompt(f"Panel URL [{url_default}]: ") or url_default).strip().rstrip("/")
        token_prompt = "Worker token (from Scrapeboard → Admin → Workers)"
        if env_token:
            token_prompt += " [env set — Enter to use]"
        token = (prompt(f"{token_prompt}: ") or env_token).strip()
        if not token:
            raise SystemExit("[fatal] worker token is required. Create a worker in the panel first.")

        default_name = env_name or platform.node() or "worker"
        name = (prompt(f"Worker name [{default_name}]: ") or default_name).strip()
        engine = (prompt("Default browser engine for local selftest [chrome]: ") or "chrome").strip().lower()
        work = (prompt("Work directory [auto temp]: ") or "").strip()

        # Optional Tailscale (default off). Detect existing install for operator awareness.
        ts_present = bool(tailscale_cli_path())
        print(flush=True)
        print("Optional Tailscale (mesh VPN — useful for private reachability; not required).", flush=True)
        if ts_present:
            print(f"  Detected Tailscale CLI: {tailscale_cli_path()}", flush=True)
            ts_q = "Enable Tailscale for this worker? (already installed)"
        else:
            ts_q = "Install/enable Tailscale on this machine?"
        want_ts = want_ts or _prompt_yes_no(prompt, ts_q, default=False)

    if engine not in ("chrome", "google-chrome", "edge", "brave", "camoufox"):
        engine = "chrome"

    cfg = {
        "panel_url": panel_url,
        "token": token,
        "worker_name": name,
        "default_engine": engine,
        "work_dir": work,
        "skip_setup": False,
        "max_browsers": 2,
        "tailscale_enabled": bool(want_ts),
        "scrape": {},  # filled from panel worker settings on heartbeat
    }
    save_config(cfg)

    if cfg["tailscale_enabled"]:
        # Install package best-effort; never block on `tailscale up` login.
        ensure_tailscale(interactive=not assume)

    print(flush=True)
    print("Next: dependencies + browser will auto-install on first job / --selftest.", flush=True)
    print(f"Start (foreground):  {sys.executable} agent.py", flush=True)
    if HOST_OS == "Windows":
        print("Background service:   install_service.bat", flush=True)
    else:
        print("Background service:   bash install_service.sh", flush=True)
    print("Toggle Tailscale later: set \"tailscale_enabled\": true|false in worker_config.json", flush=True)
    if cfg["tailscale_enabled"]:
        print("If Tailscale is installed but logged out, run:  tailscale up", flush=True)
    print("=" * 62, flush=True)
    return cfg


# ---------------------------------------------------------------------------
# Optional Tailscale (best-effort; never required for the lease loop)
# ---------------------------------------------------------------------------

def tailscale_cli_path() -> str | None:
    """Return path to tailscale CLI if found on PATH or common install locations."""
    import shutil

    found = shutil.which("tailscale")
    if found:
        return found
    candidates = []
    if HOST_OS == "Darwin":
        candidates = [
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
            "/usr/local/bin/tailscale",
            "/opt/homebrew/bin/tailscale",
        ]
    elif HOST_OS == "Windows":
        candidates = [
            r"C:\Program Files\Tailscale\tailscale.exe",
            r"C:\Program Files (x86)\Tailscale\tailscale.exe",
        ]
    else:
        candidates = ["/usr/bin/tailscale", "/usr/local/bin/tailscale"]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def _run_cmd(cmd: list[str], *, check: bool = False, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def tailscale_status_summary(cli: str | None = None) -> tuple[bool, str]:
    """
    Return (ok, message). ok means Tailscale appears logged-in / connected.
    Non-fatal helper — callers must not abort the worker on False.
    """
    cli = cli or tailscale_cli_path()
    if not cli:
        return False, "Tailscale CLI not found"
    try:
        # Prefer JSON when available
        r = _run_cmd([cli, "status", "--json"], timeout=15)
        if r.returncode == 0 and r.stdout.strip().startswith("{"):
            try:
                data = json.loads(r.stdout)
                backend = (data.get("BackendState") or "").strip()
                self_info = data.get("Self") or {}
                dns = self_info.get("DNSName") or self_info.get("HostName") or ""
                ips = self_info.get("TailscaleIPs") or []
                ip0 = ips[0] if ips else ""
                if backend.lower() in ("running",):
                    detail = " ".join(x for x in (backend, ip0, dns) if x).strip()
                    return True, detail or "running"
                return False, backend or "not running"
            except json.JSONDecodeError:
                pass
        r2 = _run_cmd([cli, "status"], timeout=15)
        out = (r2.stdout or r2.stderr or "").strip()
        if r2.returncode == 0 and out and "Logged out" not in out:
            first = out.splitlines()[0][:120]
            return True, first
        if "Logged out" in out or "needs login" in out.lower():
            return False, "logged out — run: tailscale up"
        return False, (out.splitlines()[0] if out else f"exit {r2.returncode}")[:120]
    except Exception as e:
        return False, f"status check failed: {e}"


def _print_tailscale_manual_hints() -> None:
    print("[tailscale] Manual install / login (admin/sudo may be required):", flush=True)
    if HOST_OS == "Linux":
        print("  curl -fsSL https://tailscale.com/install.sh | sh", flush=True)
        print("  sudo systemctl enable --now tailscaled", flush=True)
        print("  sudo tailscale up", flush=True)
    elif HOST_OS == "Darwin":
        print("  brew install --cask tailscale   # or install from https://tailscale.com/download", flush=True)
        print("  open -a Tailscale               # then Sign in from the menu bar", flush=True)
        print("  # or:  /Applications/Tailscale.app/Contents/MacOS/Tailscale up", flush=True)
    elif HOST_OS == "Windows":
        print("  winget install Tailscale.Tailscale", flush=True)
        print("  # or: https://tailscale.com/download/windows", flush=True)
        print("  tailscale up", flush=True)
    else:
        print("  See https://tailscale.com/download", flush=True)
    print("  Interactive browser login is usually required once.", flush=True)


def ensure_tailscale(*, interactive: bool = False, allow_install: bool | None = None) -> None:
    """
    Best-effort: detect or install Tailscale and remind about `tailscale up`.
    Never raises — worker must keep working without Tailscale.

    allow_install: defaults to True only when interactive (wizard). Service start
    should check/remind only so every restart does not re-run package managers.
    """
    if allow_install is None:
        allow_install = bool(interactive)

    cli = tailscale_cli_path()
    if cli:
        ok, msg = tailscale_status_summary(cli)
        if ok:
            print(f"[tailscale] OK — {msg}", flush=True)
            return
        print(f"[tailscale] installed but not ready: {msg}", flush=True)
        print("[tailscale] Run `tailscale up` (may open a browser / need admin) then restart the worker.", flush=True)
        if interactive:
            _print_tailscale_manual_hints()
        return

    print("[tailscale] CLI not found.", flush=True)
    if not allow_install:
        print("[tailscale] Skipping auto-install (service/start check). Commands:", flush=True)
        _print_tailscale_manual_hints()
        return

    print("[tailscale] Attempting best-effort install…", flush=True)
    installed = False
    try:
        if HOST_OS == "Linux":
            # Official install script; needs root for packages + tailscaled.
            script = "curl -fsSL https://tailscale.com/install.sh | sh"
            print(f"[tailscale] running: {script}", flush=True)
            r = _run_cmd(["bash", "-lc", script], timeout=300)
            if r.returncode != 0:
                print((r.stderr or r.stdout or "")[:400], flush=True)
                print("[tailscale] install failed (often needs sudo). Commands:", flush=True)
                _print_tailscale_manual_hints()
            else:
                installed = True
                _run_cmd(["bash", "-lc", "systemctl enable --now tailscaled 2>/dev/null || sudo systemctl enable --now tailscaled"], timeout=60)
        elif HOST_OS == "Darwin":
            brew = None
            import shutil

            brew = shutil.which("brew")
            if brew:
                print("[tailscale] brew install --cask tailscale …", flush=True)
                r = _run_cmd([brew, "install", "--cask", "tailscale"], timeout=600)
                if r.returncode == 0:
                    installed = True
                else:
                    print((r.stderr or r.stdout or "")[:400], flush=True)
            if not installed:
                print("[tailscale] Install the macOS app (Homebrew cask or download), then Sign in.", flush=True)
                _print_tailscale_manual_hints()
        elif HOST_OS == "Windows":
            import shutil

            winget = shutil.which("winget")
            if winget:
                print("[tailscale] winget install Tailscale.Tailscale …", flush=True)
                r = _run_cmd(
                    [winget, "install", "-e", "--id", "Tailscale.Tailscale", "--accept-package-agreements", "--accept-source-agreements"],
                    timeout=600,
                )
                if r.returncode == 0:
                    installed = True
                else:
                    print((r.stderr or r.stdout or "")[:400], flush=True)
            if not installed:
                print("[tailscale] Install from winget or https://tailscale.com/download/windows", flush=True)
                _print_tailscale_manual_hints()
        else:
            _print_tailscale_manual_hints()
    except Exception as e:
        print(f"[tailscale] install attempt error: {e}", flush=True)
        _print_tailscale_manual_hints()

    cli = tailscale_cli_path()
    if cli:
        print(f"[tailscale] CLI available: {cli}", flush=True)
        print("[tailscale] Complete login with:  tailscale up   (interactive; admin may be required)", flush=True)
        # Do not run `tailscale up` non-interactively — it needs browser auth.
        ok, msg = tailscale_status_summary(cli)
        if ok:
            print(f"[tailscale] OK — {msg}", flush=True)
        else:
            print(f"[tailscale] status: {msg}", flush=True)
    elif installed:
        print("[tailscale] Install finished but CLI not on PATH yet — open a new shell or reboot, then `tailscale up`.", flush=True)
    if interactive and not (cli and tailscale_status_summary(cli)[0]):
        print("[tailscale] Worker will continue without Tailscale until you finish login.", flush=True)


def remind_tailscale_if_enabled(cfg: dict | None) -> None:
    """On agent start: if config asks for Tailscale, check status (non-fatal)."""
    if not cfg:
        return
    enabled = cfg.get("tailscale_enabled")
    if enabled is None:
        enabled = cfg.get("tailscale")  # alias
    if not enabled:
        return
    print("[tailscale] enabled in worker_config.json — checking…", flush=True)
    try:
        ensure_tailscale(interactive=False)
    except Exception as e:
        print(f"[tailscale] check skipped: {e}", flush=True)


def _host_stats():
    """CPU, RAM, disk, load averages for heartbeat telemetry."""
    out = {
        "cpu": 0.0,
        "mem": 0.0,
        "disk": 0.0,
        "mem_used_gb": 0.0,
        "mem_total_gb": 0.0,
        "disk_used_gb": 0.0,
        "disk_total_gb": 0.0,
        "load_1": 0.0,
        "load_5": 0.0,
        "load_15": 0.0,
        "hostname": "",
        "os": HOST_OS,
    }
    try:
        import socket

        out["hostname"] = socket.gethostname()[:128]
    except Exception:
        pass
    try:
        import psutil

        out["cpu"] = float(psutil.cpu_percent(interval=None))
        vm = psutil.virtual_memory()
        out["mem"] = float(vm.percent)
        out["mem_used_gb"] = round(vm.used / (1024**3), 2)
        out["mem_total_gb"] = round(vm.total / (1024**3), 2)
        root = "C:\\" if HOST_OS == "Windows" else "/"
        du = psutil.disk_usage(root)
        out["disk"] = float(du.percent)
        out["disk_used_gb"] = round(du.used / (1024**3), 2)
        out["disk_total_gb"] = round(du.total / (1024**3), 2)
        try:
            load = psutil.getloadavg()
            out["load_1"], out["load_5"], out["load_15"] = (round(float(x), 2) for x in load)
        except (AttributeError, OSError):
            pass
    except Exception:
        pass
    return out


def _cpu_mem():
    s = _host_stats()
    return s["cpu"], s["mem"]


class PanelClient:
    def __init__(self, base: str, token: str, worker_name: str = ""):
        import requests

        self.requests = requests
        self.base = base.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.worker_name = worker_name

    def hello(self):
        stats = _host_stats()
        r = self.requests.post(
            f"{self.base}/api/worker-api/hello",
            json={
                "version": VERSION,
                "name": self.worker_name,
                "os": HOST_OS,
                "hostname": stats.get("hostname") or "",
            },
            headers=self.headers,
            timeout=20,
        )
        r.raise_for_status()
        return r.json()

    def heartbeat(self):
        stats = _host_stats()
        payload = {
            "cpu": stats["cpu"],
            "mem": stats["mem"],
            "disk": stats["disk"],
            "mem_used_gb": stats["mem_used_gb"],
            "mem_total_gb": stats["mem_total_gb"],
            "disk_used_gb": stats["disk_used_gb"],
            "disk_total_gb": stats["disk_total_gb"],
            "load_1": stats["load_1"],
            "load_5": stats["load_5"],
            "load_15": stats["load_15"],
            "hostname": stats["hostname"],
            "version": VERSION,
            "name": self.worker_name,
            "os": HOST_OS,
        }
        r = self.requests.post(
            f"{self.base}/api/worker-api/heartbeat",
            json=payload,
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

    def push_logs(self, lines: list[str], *, replace: bool = False):
        if not lines:
            return None
        r = self.requests.post(
            f"{self.base}/api/worker-api/logs",
            json={"lines": lines, "replace": replace},
            headers=self.headers,
            timeout=20,
        )
        r.raise_for_status()
        return r.json()

    def report_update_status(self, status: str, *, message: str = "", ref: str | None = None):
        payload: dict = {"status": status, "message": (message or "")[:2000]}
        if ref is not None:
            payload["ref"] = ref
        r = self.requests.post(
            f"{self.base}/api/worker-api/update-status",
            json=payload,
            headers=self.headers,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()


def _run_fixed_worker_update(ref: str) -> tuple[bool, str]:
    """Run the fixed worker update path only (no arbitrary shell from the panel).

    Preserves role-based sparse-checkout via install.py --role worker --update.
    """
    if not (REPO_ROOT / ".git").exists():
        return False, "not a git checkout — clone the repo (worker role) to enable remote updates"
    install_py = REPO_ROOT / "install.py"
    if not install_py.is_file():
        return False, f"missing {install_py}"

    wanted = (ref or "main").strip() or "main"
    env = os.environ.copy()
    env["SCRAPEBOARD_ASSUME_YES"] = "1"
    env["SCRAPEBOARD_UPDATE_REF"] = wanted

    cmd = [
        sys.executable,
        str(install_py),
        "--role",
        "worker",
        "--update",
        "--yes",
        "--ref",
        wanted,
    ]
    print(f"[worker] running update: {' '.join(cmd)}", flush=True)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
    except subprocess.TimeoutExpired:
        return False, "update timed out after 900s"
    except Exception as e:
        return False, f"update failed to start: {e}"

    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    tail = out[-1500:] if out else ""
    if proc.returncode != 0:
        msg = f"update exited {proc.returncode}"
        if tail:
            msg = f"{msg}: {tail}"
        return False, msg
    msg = "update ok"
    if tail:
        msg = f"{msg}; {tail.splitlines()[-1][:200]}"
    return True, msg


def _schedule_service_restart_hint() -> None:
    """Best-effort note; KeepAlive/systemd/schtasks restart on process exit."""
    if HOST_OS == "Windows":
        print("[worker] exiting so Task Scheduler can restart with new code", flush=True)
    elif HOST_OS == "Darwin":
        print("[worker] exiting so LaunchAgent KeepAlive can restart with new code", flush=True)
    else:
        print("[worker] exiting so systemd user service can restart with new code", flush=True)


class LogTailer:
    """Tail a log file and return only new lines (for panel live logs)."""

    def __init__(self, path: Path | None, max_lines: int = 200):
        self.path = path
        self.max_lines = max_lines
        self._pos = 0
        self._inode: int | None = None
        self._primed = False

    def read_new(self) -> list[str]:
        if not self.path or not self.path.exists():
            return []
        try:
            st = self.path.stat()
            inode = getattr(st, "st_ino", None)
            if self._inode is not None and inode is not None and inode != self._inode:
                self._pos = 0
            self._inode = inode
            size = st.st_size
            if size < self._pos:
                self._pos = 0
            with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
                if not self._primed:
                    # First call: send last N lines as a baseline, then stream deltas.
                    fh.seek(0, os.SEEK_END)
                    end = fh.tell()
                    start = max(0, end - 120_000)
                    fh.seek(start)
                    data = fh.read()
                    self._pos = fh.tell()
                    self._primed = True
                    lines = data.splitlines()[-self.max_lines :]
                    return lines
                fh.seek(self._pos)
                data = fh.read()
                self._pos = fh.tell()
            if not data:
                return []
            return data.splitlines()[-self.max_lines :]
        except OSError as e:
            print(f"[worker] log tail error: {e}", flush=True)
            return []


# ring buffer when not logging to a file (interactive mode)
_MEM_LOG: list[str] = []
_MEM_LOG_LOCK = threading.Lock()
_MEM_LOG_MAX = 300


class _TeeTextIO:
    """Mirror writes to the original stream and an in-memory ring."""

    def __init__(self, inner):
        self._inner = inner

    def write(self, s):
        if s:
            with _MEM_LOG_LOCK:
                for line in str(s).splitlines():
                    if line:
                        _MEM_LOG.append(line)
                while len(_MEM_LOG) > _MEM_LOG_MAX:
                    _MEM_LOG.pop(0)
        return self._inner.write(s)

    def flush(self):
        return self._inner.flush()

    def fileno(self):
        return self._inner.fileno()

    def isatty(self):
        return self._inner.isatty()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def enable_memory_log_tee() -> None:
    if not isinstance(sys.stdout, _TeeTextIO):
        sys.stdout = _TeeTextIO(sys.stdout)  # type: ignore[assignment]
    if not isinstance(sys.stderr, _TeeTextIO):
        sys.stderr = _TeeTextIO(sys.stderr)  # type: ignore[assignment]


def drain_memory_log() -> list[str]:
    with _MEM_LOG_LOCK:
        lines = list(_MEM_LOG)
        _MEM_LOG.clear()
        return lines


def _zip_dir(src: Path, dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in src.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(src).as_posix())
    return dest


_SETUP_DONE_ENGINES: set[str] = set()


def warn_insecure_panel_url(url: str) -> None:
    u = (url or "").strip().lower()
    if u.startswith("https://"):
        return
    local = "127.0.0.1" in u or "localhost" in u or u.startswith("http://[::1]")
    if local:
        print("[worker] panel URL is HTTP localhost (dev only)", flush=True)
        return
    print(
        "[worker] WARNING: panel URL is not HTTPS. Worker tokens and job data "
        "will travel in cleartext. Use https://scrape.cvmso.com in production.",
        flush=True,
    )


def sync_local_config_from_panel(hb: dict, config_file: Path | None = None) -> None:
    """Write panel worker settings into local worker_config.json (scrape + caps)."""
    scrape = hb.get("worker_config")
    if not isinstance(scrape, dict):
        return
    path = config_file or config_path()
    cfg: dict = {}
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f) or {}
        except Exception:
            cfg = {}
    prev = cfg.get("scrape") if isinstance(cfg.get("scrape"), dict) else {}
    merged = dict(scrape)
    if not merged.get("captcha_key") and prev.get("captcha_key"):
        merged["captcha_key"] = prev["captcha_key"]
    if not merged.get("captcha_backup_key") and prev.get("captcha_backup_key"):
        merged["captcha_backup_key"] = prev["captcha_backup_key"]
    cfg["scrape"] = merged
    if hb.get("max_browsers") is not None:
        try:
            cfg["max_browsers"] = int(hb["max_browsers"])
        except (TypeError, ValueError):
            pass
    if hb.get("name"):
        cfg["worker_name"] = str(hb["name"])
    if scrape.get("engine"):
        cfg["default_engine"] = scrape["engine"]
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)


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


def run_chunk(
    job: dict,
    chunk: dict,
    work_dir: Path,
    skip_setup: bool = False,
    stop: threading.Event | None = None,
) -> tuple[int, Path | None]:
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
    if not settings.get("browser_path"):
        args.browser_path = None

    out_dir = work_dir / "out" / str(chunk["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    stop_event = stop if stop is not None else threading.Event()
    ts = str(job.get("ts") or "run")

    rows, _failed = gs.execute_index_batch(
        args,
        keywords,
        [gs.format_location(l) for l in locations],
        int(chunk["start"]),
        int(chunk["end"]),
        str(out_dir),
        ts,
        stop_event,
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
    p.add_argument(
        "--setup",
        action="store_true",
        help="Re-run first-run wizard (writes worker_config.json), then continue",
    )
    p.add_argument("--selftest", action="store_true", help="Verify browser/stealth locally, then exit")
    p.add_argument(
        "--engine",
        default="",
        help="Browser engine for --selftest / first bootstrap (default: chrome or config)",
    )
    p.add_argument("--skip-setup", action="store_true", help="Do not auto-install browsers/deps")
    p.add_argument("--force-setup", action="store_true", help="Re-run browser/deps install")
    p.add_argument(
        "--service",
        action="store_true",
        help="Service mode: log to logs/worker.log, stable work/ dir (for install_service.*)",
    )
    p.add_argument(
        "--log-file",
        default="",
        help="Append stdout/stderr to this file (default with --service: logs/worker.log)",
    )
    return p.parse_args(argv)


def setup_service_logging(log_file: Path) -> None:
    """Redirect stdout/stderr to a rotating-friendly append log for headless runs."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    # Line-buffered text log so operators can `tail -f`
    stream = open(log_file, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = stream  # type: ignore[assignment]
    sys.stderr = stream  # type: ignore[assignment]
    print(f"\n========== scrapeboard worker v{VERSION} start {time.strftime('%Y-%m-%d %H:%M:%S')} ==========", flush=True)


def default_service_paths() -> dict[str, Path]:
    logs = ROOT / "logs"
    work = ROOT / "work"
    return {"logs": logs, "work": work, "log_file": logs / "worker.log"}


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
        "config_path": str(cfg_path),
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

    # Resolve config / wizard before redirecting stdout (wizard needs a TTY)
    rt = resolve_runtime(args)

    paths = default_service_paths()
    log_path: Path | None = None
    if args.service or args.log_file:
        log_path = Path(args.log_file) if args.log_file else paths["log_file"]
        setup_service_logging(log_path)
    else:
        enable_memory_log_tee()
    if args.service:
        print(f"[worker] service mode ({SERVICE_NAME})", flush=True)

    # Optional Tailscale: remind/check only — never block the lease loop
    try:
        cfg_ts = load_config()
        if cfg_ts is None and Path(rt["config_path"]).exists():
            with open(rt["config_path"], encoding="utf-8") as f:
                cfg_ts = json.load(f)
        remind_tailscale_if_enabled(cfg_ts)
    except Exception as e:
        print(f"[tailscale] config check skipped: {e}", flush=True)

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
    warn_insecure_panel_url(rt["panel_url"])
    if rt["work_dir"]:
        work_root = Path(rt["work_dir"])
    elif args.service:
        work_root = paths["work"]
    else:
        work_root = Path(tempfile.mkdtemp(prefix="scrapeboard_worker_"))
    work_root.mkdir(parents=True, exist_ok=True)

    print(
        f"[worker] v{VERSION} os={HOST_OS} name={rt['worker_name']}\n"
        f"[worker] panel={rt['panel_url']}  work={work_root}",
        flush=True,
    )

    try:
        hello = client.hello()
        print(
            f"[worker] connected as id={hello.get('worker_id')} "
            f"name={hello.get('name')} enabled={hello.get('enabled')}",
            flush=True,
        )
        if not hello.get("enabled", True):
            print("[worker] panel reports worker disabled — waiting until enabled", flush=True)
    except Exception as e:
        print(f"[fatal] cannot connect to panel: {e}", flush=True)
        print("  Check panel URL, worker token, and that the panel is reachable.", flush=True)
        return 1

    # Concurrent user instances: each lease runs in its own thread under
    # work_root/user_{owner_id}/{job_id}/ so users never share folders.
    active: dict[str, threading.Thread] = {}
    stops: dict[str, threading.Event] = {}
    active_lock = threading.Lock()
    log_tailer = LogTailer(log_path)
    last_log_push = 0.0
    try:
        cfg0 = load_config() or {}
        max_slots = max(1, int(cfg0.get("max_browsers") or 2))
    except Exception:
        max_slots = 2

    def _instance_key(job: dict, chunk: dict) -> str:
        return f"{job.get('job_id')}:{chunk.get('id')}"

    def _run_instance(job: dict, chunk: dict, stop: threading.Event) -> None:
        key = _instance_key(job, chunk)
        owner_id = job.get("owner_id")
        if owner_id is None:
            # derive from public_id prefix when panel is older
            try:
                owner_id = int(str(job.get("job_id") or "").split("_", 1)[0])
            except (TypeError, ValueError, IndexError):
                owner_id = "unknown"
        instance_dir = work_root / f"user_{owner_id}" / str(job["job_id"])
        print(
            f"[worker] start instance user={owner_id} job={job['job_id']} "
            f"chunk={chunk['id']} dir={instance_dir}",
            flush=True,
        )
        rows = 0
        try:
            rows, zip_path = run_chunk(
                job,
                chunk,
                instance_dir,
                skip_setup=rt["skip_setup"],
                stop=stop,
            )
            if stop.is_set():
                print(
                    f"[worker] cancelled job={job['job_id']} chunk={chunk['id']}",
                    flush=True,
                )
            if zip_path and zip_path.exists() and not stop.is_set():
                try:
                    client.upload(job["job_id"], chunk["id"], zip_path)
                    print(f"[worker] uploaded chunk={chunk['id']} user={owner_id}", flush=True)
                except Exception as e:
                    print(f"[worker] upload skipped/failed: {e}", flush=True)
            elif zip_path and zip_path.exists():
                # Best-effort upload of partial results after cancel
                try:
                    client.upload(job["job_id"], chunk["id"], zip_path)
                    print(f"[worker] uploaded partial chunk={chunk['id']}", flush=True)
                except Exception as e:
                    print(f"[worker] partial upload skipped: {e}", flush=True)
        except Exception as e:
            print(f"[worker] chunk error user={owner_id} chunk={chunk['id']}: {e}", flush=True)
            rows = 0
        try:
            ack = client.ack(job["job_id"], chunk["id"], rows)
            if ack.get("cancelled"):
                print(f"[worker] ack cancelled chunk={chunk['id']}", flush=True)
            else:
                print(f"[worker] ack chunk={chunk['id']} rows={rows}", flush=True)
        except Exception as e:
            print(f"[worker] ack error: {e}", flush=True)
        with active_lock:
            active.pop(key, None)
            stops.pop(key, None)

    def _apply_cancels(cancel_jobs: list) -> None:
        if not cancel_jobs:
            return
        wanted = {str(x) for x in cancel_jobs}
        hit = False
        with active_lock:
            for key, ev in list(stops.items()):
                job_id = key.split(":", 1)[0]
                if job_id in wanted and not ev.is_set():
                    ev.set()
                    hit = True
                    print(f"[worker] cancel signal for job={job_id}", flush=True)
        if hit:
            try:
                gs.kill_active_browsers()
            except Exception as e:
                print(f"[worker] browser kill on cancel: {e}", flush=True)

    def _push_logs(force: bool = False) -> None:
        nonlocal last_log_push
        now = time.time()
        if not force and now - last_log_push < 4:
            return
        try:
            if log_path:
                lines = log_tailer.read_new()
            else:
                lines = drain_memory_log()
            if lines:
                client.push_logs(lines, replace=False)
            last_log_push = now
        except Exception as e:
            print(f"[worker] log push failed: {e}", flush=True)

    def _maybe_apply_panel_update(hb: dict) -> bool:
        """If panel queued an update, wait for idle, run fixed update, exit for restart.

        Returns True when the process should exit (service KeepAlive restarts new code).
        """
        cmds = hb.get("commands") or []
        upd = hb.get("update")
        if "update" not in cmds and not isinstance(upd, dict):
            return False
        ref = "main"
        if isinstance(upd, dict):
            ref = str(upd.get("ref") or "main").strip() or "main"
        print(f"[worker] panel requested update (ref={ref})", flush=True)
        try:
            client.report_update_status("updating", message="waiting for active jobs", ref=ref)
        except Exception as e:
            print(f"[worker] update-status report failed: {e}", flush=True)

        deadline = time.time() + 600
        while time.time() < deadline:
            with active_lock:
                for k, t in list(active.items()):
                    if not t.is_alive():
                        active.pop(k, None)
                        stops.pop(k, None)
                n = len(active)
            if n == 0:
                break
            print(f"[worker] update: waiting for {n} instance(s) to finish…", flush=True)
            time.sleep(5)
        else:
            msg = "timed out waiting for active jobs (10m); re-queue update when idle"
            print(f"[worker] {msg}", flush=True)
            try:
                client.report_update_status("failed", message=msg, ref=ref)
            except Exception as e:
                print(f"[worker] update-status report failed: {e}", flush=True)
            return False

        try:
            client.report_update_status("updating", message="running git/pip update", ref=ref)
        except Exception as e:
            print(f"[worker] update-status report failed: {e}", flush=True)

        ok, message = _run_fixed_worker_update(ref)
        try:
            client.report_update_status(
                "success" if ok else "failed",
                message=message,
                ref=ref,
            )
        except Exception as e:
            print(f"[worker] update-status report failed: {e}", flush=True)

        if not ok:
            print(f"[worker] update failed: {message}", flush=True)
            return False

        print(f"[worker] update succeeded: {message}", flush=True)
        _push_logs(force=True)
        _schedule_service_restart_hint()
        return True

    while True:
        try:
            hb = client.heartbeat()
            try:
                sync_local_config_from_panel(hb, Path(rt["config_path"]))
            except Exception as e:
                print(f"[worker] config sync warning: {e}", flush=True)
            _apply_cancels(hb.get("cancel_jobs") or [])
            _push_logs()
            if _maybe_apply_panel_update(hb):
                # Exit 0 so systemd / LaunchAgent / schtasks KeepAlive restarts new code.
                sys.exit(0)
            if hb.get("max_browsers") is not None:
                try:
                    max_slots = max(1, int(hb["max_browsers"]))
                except (TypeError, ValueError):
                    pass
            if not hb.get("enabled", True):
                print("[worker] disabled by panel; sleeping", flush=True)
                time.sleep(10)
                continue
            if hb.get("drain"):
                with active_lock:
                    running = len(active)
                if running:
                    print(f"[worker] draining; waiting on {running} instance(s)", flush=True)
                else:
                    print("[worker] draining; no new leases", flush=True)
                time.sleep(5)
                continue

            with active_lock:
                # prune dead threads
                for k, t in list(active.items()):
                    if not t.is_alive():
                        active.pop(k, None)
                        stops.pop(k, None)
                slots_free = max_slots - len(active)

            if slots_free <= 0:
                time.sleep(1)
                continue

            # Fill free slots (one lease attempt per free slot per loop)
            leased_any = False
            for _ in range(slots_free):
                lease = client.lease()
                if lease.get("slots_full"):
                    break
                chunk = lease.get("chunk")
                if not chunk:
                    break
                job = lease["job"]
                key = _instance_key(job, chunk)
                with active_lock:
                    if key in active:
                        break
                    stop_ev = threading.Event()
                    t = threading.Thread(
                        target=_run_instance,
                        args=(job, chunk, stop_ev),
                        name=f"scrape-{key}",
                        daemon=True,
                    )
                    active[key] = t
                    stops[key] = stop_ev
                    t.start()
                    leased_any = True
                print(
                    f"[worker] leased job={job['job_id']} chunk={chunk['id']} "
                    f"owner={job.get('owner_id')} "
                    f"engine={job.get('settings', {}).get('engine')} "
                    f"threads={job.get('settings', {}).get('threads')} "
                    f"slots={len(active)}/{max_slots}",
                    flush=True,
                )
            if not leased_any:
                time.sleep(2)
        except KeyboardInterrupt:
            print("[worker] stop — waiting for instances…", flush=True)
            try:
                gs.shutdown_all("worker interrupt")
            except Exception:
                pass
            with active_lock:
                for ev in stops.values():
                    ev.set()
                threads = list(active.values())
            for t in threads:
                t.join(timeout=30)
            return 0
        except Exception as e:
            print(f"[worker] loop error: {e}; retry in 5s", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
