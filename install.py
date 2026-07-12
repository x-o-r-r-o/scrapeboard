#!/usr/bin/env python3
"""
Scrapeboard — single interactive install entry.

Operators run once via:
  ./install.sh          (macOS / Linux)
  install.bat           (Windows)
  ./install.command     (macOS double-click)
  python3 install.py    (any OS with Python 3.10+)

Routes to control-panel or worker setup for the detected OS.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_PY = (3, 10)


def detect_os() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    if system == "windows" or sys.platform.startswith("win"):
        return "windows"
    return "other"


def os_label(kind: str) -> str:
    return {
        "macos": "macOS",
        "linux": "Linux",
        "windows": "Windows",
        "other": platform.system() or "unknown",
    }.get(kind, kind)


def hestia_detected() -> bool:
    if Path("/usr/local/hestia").is_dir():
        return True
    return shutil.which("v-rebuild-web-domain") is not None


def is_root() -> bool:
    if hasattr(os, "geteuid"):
        return os.geteuid() == 0
    return False


def banner(kind: str) -> None:
    print("=" * 64)
    print(" Scrapeboard — install")
    print("=" * 64)
    print(f" Detected OS: {os_label(kind)}")
    print(f" Repo root:   {ROOT}")
    print("=" * 64)
    print()


def prompt(msg: str, default: str | None = None) -> str:
    hint = f" [{default}]" if default is not None else ""
    try:
        raw = input(f"{msg}{hint}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(130)
    if not raw and default is not None:
        return default
    return raw


def prompt_yes_no(msg: str, *, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    ans = prompt(f"{msg} [{suffix}]", "").lower()
    if not ans:
        return default
    return ans in ("y", "yes")


def choose(options: list[tuple[str, str]], *, default: str) -> str:
    """options: (key, description). Returns selected key."""
    keys = {k.lower() for k, _ in options}
    default_l = default.lower()
    print()
    for key, desc in options:
        mark = " (default)" if key.lower() == default_l else ""
        print(f"  {key}) {desc}{mark}")
    print()
    while True:
        ans = prompt("Choice", default).lower()
        if ans in keys:
            return ans
        print(f"  Enter one of: {', '.join(k for k, _ in options)}")


def run(cmd: list[str] | str, *, cwd: Path | None = None, shell: bool = False) -> int:
    print()
    print(f"→ {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    print()
    try:
        return subprocess.call(cmd, cwd=str(cwd or ROOT), shell=shell)
    except FileNotFoundError as exc:
        print(f"ERROR: command not found: {exc.filename or cmd}")
        return 127


def exec_replace(cmd: list[str], *, cwd: Path | None = None) -> None:
    """Hand off to another process (Unix). Falls back to subprocess on Windows."""
    print()
    print(f"→ {' '.join(cmd)}")
    print()
    work = str(cwd or ROOT)
    if detect_os() == "windows":
        code = subprocess.call(cmd, cwd=work)
        sys.exit(code)
    os.chdir(work)
    os.execvp(cmd[0], cmd)


def ensure_python() -> None:
    if sys.version_info < MIN_PY:
        print(
            f"ERROR: Python {MIN_PY[0]}.{MIN_PY[1]}+ required "
            f"(found {sys.version_info.major}.{sys.version_info.minor})."
        )
        sys.exit(1)


def path_exists(rel: str) -> bool:
    return (ROOT / rel).exists()


# ── Control panel ──────────────────────────────────────────────────────────


def ensure_deploy_config() -> Path:
    cfg = ROOT / "deploy" / "config.env"
    example = ROOT / "deploy" / "config.env.example"
    if cfg.is_file():
        return cfg
    if not example.is_file():
        print("ERROR: deploy/config.env.example missing.")
        sys.exit(1)
    print("No deploy/config.env yet — copying from config.env.example.")
    shutil.copy2(example, cfg)
    print(f"Created {cfg}")
    print("Edit BOOTSTRAP_ADMIN_PASSWORD (and DOMAIN / HESTIA_USER if needed) before production install.")
    if prompt_yes_no("Open a short pause so you can edit deploy/config.env now?", default=True):
        print(f"  Edit: {cfg}")
        prompt("Press Enter when ready to continue", "")
    return cfg


def run_hestia_install() -> int:
    script = ROOT / "deploy" / "hestiacp" / "install.sh"
    if not script.is_file():
        print(f"ERROR: missing {script}")
        return 1
    if not is_root():
        print("HestiaCP install must run as root (ssh root@server).")
        print(f"  sudo bash {script}")
        if prompt_yes_no("Re-run with sudo now?", default=True):
            return run(["sudo", "bash", str(script)])
        print("Aborted. See deploy/hestiacp/README.md")
        return 1
    ensure_deploy_config()
    return run(["bash", str(script)])


def prepare_local_backend() -> Path:
    backend = ROOT / "panel" / "backend"
    env_file = backend / ".env"
    example = backend / ".env.example"
    if not env_file.is_file():
        if not example.is_file():
            print(f"ERROR: missing {example}")
            sys.exit(1)
        shutil.copy2(example, env_file)
        print(f"Created {env_file} from .env.example")
        print("Edit SECRET_KEY and BOOTSTRAP_ADMIN_PASSWORD before first login.")
    return backend


def print_local_frontend_steps() -> None:
    fe = ROOT / "panel" / "frontend"
    print()
    print("Frontend (separate terminal):")
    print(f"  cd {fe}")
    print("  npm install          # or: bun install")
    print("  npm run dev          # http://127.0.0.1:5173  (proxies /api → :3010)")
    print()
    print("API health: curl -s http://127.0.0.1:3010/api/health")


def run_local_panel(kind: str) -> int:
    print()
    print("Local control panel (development)")
    print("  HestiaCP / production systemd is Linux-server only.")
    print("  This path prepares the FastAPI API and points you at the React UI.")
    print()
    backend = prepare_local_backend()

    if kind in ("macos", "linux"):
        run_sh = ROOT / "panel" / "run.sh"
        if not run_sh.is_file():
            print(f"ERROR: missing {run_sh}")
            return 1
        print_local_frontend_steps()
        if prompt_yes_no("Start local API now (panel/run.sh --reload)?", default=True):
            exec_replace(["bash", str(run_sh), "--reload"])
        print()
        print("Start later with:")
        print(f"  bash {run_sh} --reload")
        return 0

    # Windows — no panel/run.sh; mirror its steps in Python
    print("Windows: setting up panel/backend venv + deps…")
    venv = backend / ".venv"
    py = sys.executable
    if not venv.is_dir():
        code = run([py, "-m", "venv", str(venv)], cwd=backend)
        if code != 0:
            return code
    pip = venv / "Scripts" / "pip.exe"
    uvicorn = venv / "Scripts" / "uvicorn.exe"
    if not pip.is_file():
        print(f"ERROR: expected {pip}")
        return 1
    code = run([str(pip), "install", "-r", "requirements.txt"], cwd=backend)
    if code != 0:
        return code
    print_local_frontend_steps()
    print("Start API later with:")
    print(f'  "{uvicorn}" app.main:app --reload --host 127.0.0.1 --port 3010')
    print(f"  (from {backend})")
    if prompt_yes_no("Start local API now?", default=True):
        return run(
            [
                str(uvicorn),
                "app.main:app",
                "--reload",
                "--host",
                "127.0.0.1",
                "--port",
                "3010",
            ],
            cwd=backend,
        )
    return 0


def panel_menu(kind: str) -> int:
    print()
    print("Control panel setup")
    print("-" * 40)

    if kind == "linux":
        hestia = hestia_detected()
        if hestia:
            print("HestiaCP detected on this host.")
            default = "1"
        else:
            print("HestiaCP not detected (looking for /usr/local/hestia).")
            print("Production panel needs a Linux VPS with HestiaCP — see deploy/hestiacp/README.md")
            default = "2"
        choice = choose(
            [
                ("1", "Production — HestiaCP install (deploy/hestiacp/install.sh, panel-only)"),
                ("2", "Local development — API + frontend helpers"),
                ("3", "Show guided production steps only (no install)"),
                ("q", "Back / quit"),
            ],
            default=default,
        )
        if choice == "q":
            return 0
        if choice == "1":
            return run_hestia_install()
        if choice == "3":
            print_hestia_guide()
            return 0
        return run_local_panel(kind)

    # macOS / Windows / other — no Hestia
    print(f"{os_label(kind)} cannot run the HestiaCP production installer.")
    print("Use a Linux VPS with Hestia for production (deploy/hestiacp/README.md).")
    print("Here you can set up a local development panel.")
    choice = choose(
        [
            ("1", "Local development panel (API + frontend steps)"),
            ("2", "Show how to deploy production on Linux/Hestia"),
            ("q", "Back / quit"),
        ],
        default="1",
    )
    if choice == "q":
        return 0
    if choice == "2":
        print_hestia_guide()
        return 0
    return run_local_panel(kind)


def print_hestia_guide() -> None:
    print()
    print("Production panel (Linux + HestiaCP) — summary")
    print("-" * 40)
    print("1. On the VPS as root: clone this repo under /home/<user>/apps/scrapeboard")
    print("2. cp deploy/config.env.example deploy/config.env  # edit BOOTSTRAP_ADMIN_PASSWORD")
    print("3. bash deploy/hestiacp/install.sh")
    print("4. Open https://<domain> → change password → enable 2FA")
    print()
    print("Or from this entrypoint on a Hestia Linux host: choose Control panel → Production.")
    print("Full guide: deploy/hestiacp/README.md")
    print("Note: panel install excludes worker/ (sparse-checkout).")


# ── Worker ─────────────────────────────────────────────────────────────────


def worker_setup_cmd(kind: str) -> list[str] | None:
    worker = ROOT / "worker"
    if kind == "windows":
        bat = worker / "setup_and_run.bat"
        if not bat.is_file():
            return None
        return ["cmd", "/c", str(bat)]
    if kind == "macos":
        # Prefer .sh (same as .command); works when double-click wrapper already used
        sh = worker / "setup_and_run.sh"
        if sh.is_file():
            return ["bash", str(sh)]
        return None
    if kind == "linux":
        sh = worker / "setup_and_run.sh"
        if sh.is_file():
            return ["bash", str(sh)]
        return None
    return None


def worker_menu(kind: str) -> int:
    print()
    print("Worker agent setup")
    print("-" * 40)
    print("Runs worker/setup_and_run.* — venv, deps, selftest, wizard (panel URL + token).")
    print("Optional Tailscale is asked inside the worker wizard.")
    print("After setup, you can install a login/boot background service.")
    print()

    if not path_exists("worker"):
        print("ERROR: worker/ folder not found.")
        print("This checkout may be panel-only (sparse-checkout). Clone full repo on the scrape machine.")
        return 1

    cmd = worker_setup_cmd(kind)
    if not cmd:
        print("ERROR: no setup_and_run script for this OS under worker/")
        return 1

    if not prompt_yes_no("Start worker setup now?", default=True):
        print("Cancelled.")
        return 0

    # setup_and_run already prompts for service when interactive; we still
    # hand off fully so the agent can run in the foreground if they decline.
    if kind == "windows":
        code = run(cmd, cwd=ROOT / "worker")
        return code
    exec_replace(cmd, cwd=ROOT / "worker")
    return 0  # unreachable on Unix after exec


# ── Main ───────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="install.py",
        description="Scrapeboard interactive installer — control panel or worker.",
    )
    p.add_argument(
        "--role",
        choices=("panel", "worker"),
        help="Skip the role menu (panel|worker).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print detected OS and planned paths, then exit.",
    )
    return p.parse_args(argv)


def dry_run(kind: str) -> int:
    print(f"OS: {os_label(kind)} ({platform.platform()})")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Root: {ROOT}")
    print(f"Hestia detected: {hestia_detected() if kind == 'linux' else 'n/a (not Linux)'}")
    print()
    print("Panel paths:")
    if kind == "linux":
        print("  production → bash deploy/hestiacp/install.sh (root)")
    else:
        print("  production → not available here; use Linux + Hestia")
    print("  local      → panel/run.sh (Unix) or panel/backend venv (Windows)")
    print()
    print("Worker paths:")
    cmd = worker_setup_cmd(kind)
    print(f"  setup      → {' '.join(cmd) if cmd else '(missing worker/)'}")
    if kind == "windows":
        print("  service    → worker/install_service.bat")
    else:
        print("  service    → bash worker/install_service.sh")
    return 0


def main(argv: list[str] | None = None) -> int:
    ensure_python()
    args = parse_args(argv)
    kind = detect_os()

    if args.dry_run:
        return dry_run(kind)

    banner(kind)

    if kind == "other":
        print(f"Unsupported OS: {platform.system()}")
        print("Supported: macOS, Linux, Windows.")
        return 1

    role = args.role
    if not role:
        print("What do you want to set up on this machine?")
        pick = choose(
            [
                ("1", "Control panel (Hestia production or local API/UI)"),
                ("2", "Worker agent (scrape machine)"),
                ("q", "Quit"),
            ],
            default="1" if kind == "linux" and hestia_detected() else "2",
        )
        if pick == "q":
            print("Bye.")
            return 0
        role = "panel" if pick == "1" else "worker"

    if role == "panel":
        return panel_menu(kind)
    return worker_menu(kind)


if __name__ == "__main__":
    sys.exit(main())
