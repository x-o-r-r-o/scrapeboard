#!/usr/bin/env python3
"""
Scrapeboard — single interactive install entry.

Operators run once via:
  ./install.sh          (macOS / Linux)
  install.bat           (Windows)
  ./install.command     (macOS double-click)
  python3 install.py    (any OS with Python 3.10+)

Noninteractive (after role is known):
  python3 install.py --role worker --yes
  SCRAPEBOARD_PANEL_URL=… SCRAPEBOARD_TOKEN=… python3 install.py --role worker --yes
  python3 install.py --role panel --yes

Routes to control-panel or worker setup for the detected OS.
"""
from __future__ import annotations

import argparse
import os
import platform
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_PY = (3, 10)

# Set in main() from --yes / env
ASSUME_YES = False
WANT_TAILSCALE = False

# Summary collected during this run
_SUMMARY_INSTALLED: list[str] = []
_SUMMARY_MANUAL: list[str] = []


def note_installed(msg: str) -> None:
    _SUMMARY_INSTALLED.append(msg)


def note_manual(msg: str) -> None:
    _SUMMARY_MANUAL.append(msg)


def env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
        "on",
    )


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


def can_elevate() -> bool:
    if is_root():
        return True
    if detect_os() == "windows":
        return False
    if shutil.which("sudo") and subprocess.call(
        ["sudo", "-n", "true"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) == 0:
        return True
    return False


def banner(kind: str) -> None:
    print("=" * 64)
    print(" Scrapeboard — install")
    print("=" * 64)
    print(f" Detected OS: {os_label(kind)}")
    print(f" Repo root:   {ROOT}")
    if ASSUME_YES:
        print(" Mode:        noninteractive (--yes)")
    print("=" * 64)
    print()


def prompt(msg: str, default: str | None = None) -> str:
    if ASSUME_YES and default is not None:
        print(f"{msg} [{default}] → (assume-yes) {default}")
        return default
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
    if ASSUME_YES:
        print(f"{msg} → (assume-yes) {'yes' if default else 'no'}")
        return default
    suffix = "Y/n" if default else "y/N"
    ans = prompt(f"{msg} [{suffix}]", "").lower()
    if not ans:
        return default
    return ans in ("y", "yes")


def choose(options: list[tuple[str, str]], *, default: str) -> str:
    """options: (key, description). Returns selected key."""
    keys = {k.lower() for k, _ in options}
    default_l = default.lower()
    if ASSUME_YES:
        print(f"Choice → (assume-yes) {default_l}")
        return default_l
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


def run(cmd: list[str] | str, *, cwd: Path | None = None, shell: bool = False, env: dict | None = None) -> int:
    print()
    print(f"→ {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    print()
    merged = None
    if env is not None:
        merged = os.environ.copy()
        merged.update(env)
    try:
        return subprocess.call(cmd, cwd=str(cwd or ROOT), shell=shell, env=merged)
    except FileNotFoundError as exc:
        print(f"ERROR: command not found: {exc.filename or cmd}")
        return 127


def exec_replace(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None) -> None:
    """Hand off to another process (Unix). Falls back to subprocess on Windows."""
    print()
    print(f"→ {' '.join(cmd)}")
    print()
    work = str(cwd or ROOT)
    if env:
        os.environ.update(env)
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


def print_install_summary() -> None:
    print()
    print("=" * 64)
    print(" Install summary")
    print("=" * 64)
    if _SUMMARY_INSTALLED:
        print("Installed / prepared:")
        for item in _SUMMARY_INSTALLED:
            print(f"  - {item}")
    else:
        print("Installed / prepared: (see role-specific script output)")
    if _SUMMARY_MANUAL:
        print("Still needs manual input / action:")
        for item in _SUMMARY_MANUAL:
            print(f"  - {item}")
    else:
        print("Still needs manual input / action: (none recorded here)")
    print("=" * 64)


# ── Ensure helpers (python / venv / pip / node) ─────────────────────────────


def which_node() -> str | None:
    for name in ("node", "nodejs"):
        found = shutil.which(name)
        if found:
            return found
    return None


def which_npm() -> str | None:
    return shutil.which("npm")


def which_bun() -> str | None:
    return shutil.which("bun")


def ensure_node_best_effort(kind: str) -> bool:
    """Try to get Node.js on PATH when --yes and privileges allow. Returns True if node available."""
    if which_node():
        return True
    if not ASSUME_YES:
        note_manual("Install Node.js 18+ (or Bun) for panel frontend")
        return False

    print("Node.js not found — attempting best-effort install (--yes)…")
    if kind == "macos":
        brew = shutil.which("brew") or (
            "/opt/homebrew/bin/brew"
            if Path("/opt/homebrew/bin/brew").is_file()
            else "/usr/local/bin/brew"
            if Path("/usr/local/bin/brew").is_file()
            else None
        )
        if not brew or not Path(brew).exists():
            note_manual("Install Homebrew (https://brew.sh), then: brew install node")
            return False
        code = run([brew, "install", "node"])
        if code == 0 and which_node():
            note_installed("node (Homebrew)")
            return True
        note_manual("brew install node")
        return False

    if kind == "linux" and can_elevate():
        apt = shutil.which("apt-get")
        if apt:
            prefix = [] if is_root() else ["sudo", "-n"]
            run(
                prefix
                + ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "update", "-qq"]
            )
            code = run(
                prefix
                + [
                    "env",
                    "DEBIAN_FRONTEND=noninteractive",
                    "apt-get",
                    "install",
                    "-y",
                    "nodejs",
                    "npm",
                ]
            )
            if code == 0 and which_node():
                note_installed("nodejs npm (apt)")
                return True
        note_manual("Install Node.js 18+ via apt/nodesource or Bun")
        return False

    if kind == "windows" and shutil.which("winget"):
        code = run(
            [
                "winget",
                "install",
                "-e",
                "--id",
                "OpenJS.NodeJS.LTS",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ]
        )
        if code == 0:
            note_installed("Node.js LTS (winget) — open a new shell if node not on PATH yet")
            return bool(which_node())
        note_manual("winget install OpenJS.NodeJS.LTS")
        return False

    note_manual("Install Node.js 18+ or Bun for panel frontend")
    return False


def ensure_venv(python: str, venv_dir: Path) -> Path | None:
    """Create venv if missing; return path to pip executable."""
    if detect_os() == "windows":
        pip = venv_dir / "Scripts" / "pip.exe"
        py_venv = venv_dir / "Scripts" / "python.exe"
    else:
        pip = venv_dir / "bin" / "pip"
        py_venv = venv_dir / "bin" / "python"

    if py_venv.is_file() and pip.is_file():
        return pip

    if venv_dir.is_dir() and not py_venv.is_file():
        print(f"Removing broken venv at {venv_dir}")
        shutil.rmtree(venv_dir)

    print(f"Creating venv at {venv_dir}…")
    code = run([python, "-m", "venv", str(venv_dir)])
    if code != 0 or not pip.is_file():
        note_manual(f"Create venv manually: {python} -m venv {venv_dir}")
        return None
    note_installed(str(venv_dir.relative_to(ROOT)) if ROOT in venv_dir.parents or venv_dir == ROOT else str(venv_dir))
    return pip


def pip_install_requirements(pip: Path, req: Path, *, cwd: Path) -> bool:
    if not req.is_file():
        print(f"ERROR: missing {req}")
        return False
    code = run([str(pip), "install", "--upgrade", "pip"], cwd=cwd)
    if code != 0:
        return False
    # Refresh packages to latest versions matching the pin file (same as first install).
    code = run([str(pip), "install", "--upgrade", "-r", str(req)], cwd=cwd)
    if code == 0:
        note_installed(f"pip -r {req.name} ({cwd.name})")
        return True
    return False


def generate_secret_hex(n: int = 32) -> str:
    return secrets.token_hex(n)


def write_local_backend_env(env_file: Path, example: Path) -> None:
    """Create .env from example; with --yes generate secrets when placeholders remain."""
    if env_file.is_file():
        return
    if not example.is_file():
        print(f"ERROR: missing {example}")
        sys.exit(1)
    text = example.read_text(encoding="utf-8")
    if ASSUME_YES:
        secret = generate_secret_hex(32)
        admin_pass = secrets.token_urlsafe(16)
        lines = []
        for line in text.splitlines():
            if line.startswith("SECRET_KEY="):
                lines.append(f"SECRET_KEY={secret}")
            elif line.startswith("BOOTSTRAP_ADMIN_PASSWORD="):
                lines.append(f"BOOTSTRAP_ADMIN_PASSWORD={admin_pass}")
            else:
                lines.append(line)
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Created {env_file} with generated SECRET_KEY and BOOTSTRAP_ADMIN_PASSWORD")
        print(f"  BOOTSTRAP_ADMIN_PASSWORD={admin_pass}  (save this)")
        note_installed("panel/backend/.env (generated secrets)")
        note_manual("Save the generated BOOTSTRAP_ADMIN_PASSWORD shown above")
    else:
        shutil.copy2(example, env_file)
        print(f"Created {env_file} from .env.example")
        print("Edit SECRET_KEY and BOOTSTRAP_ADMIN_PASSWORD before first login.")
        note_installed("panel/backend/.env (from example)")
        note_manual("Edit panel/backend/.env secrets before first login")


def install_frontend_deps(kind: str) -> None:
    fe = ROOT / "panel" / "frontend"
    if not fe.is_dir():
        return
    ensure_node_best_effort(kind)
    bun = which_bun()
    npm = which_npm()
    if bun:
        print("Installing frontend deps with bun…")
        if run([bun, "install"], cwd=fe) == 0:
            note_installed("panel/frontend (bun install)")
        return
    if npm:
        print("Installing frontend deps with npm…")
        if run([npm, "install"], cwd=fe) == 0:
            note_installed("panel/frontend (npm install)")
        return
    note_manual("cd panel/frontend && npm install && npm run dev")


# ── Machine role (panel | worker) ───────────────────────────────────────────

ROLE_FILE = ROOT / ".scrapeboard-role"
VALID_ROLES = frozenset({"panel", "worker"})


def normalize_role(value: str | None) -> str | None:
    if value is None:
        return None
    role = value.strip().lower()
    return role if role in VALID_ROLES else None


def env_role() -> str | None:
    return normalize_role(os.environ.get("SCRAPEBOARD_ROLE"))


def read_role_file() -> str | None:
    if not ROLE_FILE.is_file():
        return None
    try:
        raw = ROLE_FILE.read_text(encoding="utf-8").strip().splitlines()
    except OSError as exc:
        print(f"ERROR: cannot read {ROLE_FILE}: {exc}")
        sys.exit(1)
    if not raw:
        return None
    role = normalize_role(raw[0])
    if role is None:
        print(f"ERROR: invalid role in {ROLE_FILE}: {raw[0]!r} (want panel|worker)")
        sys.exit(1)
    return role


def current_role() -> str | None:
    """Env override wins; else durable .scrapeboard-role."""
    return env_role() or read_role_file()


def write_role(role: str) -> None:
    role_n = normalize_role(role)
    if role_n is None:
        raise ValueError(f"invalid role: {role!r}")
    ROLE_FILE.write_text(role_n + "\n", encoding="utf-8")
    print(f"Machine role saved: {role_n} ({ROLE_FILE})")
    note_installed(f"role={role_n}")


def ensure_role(desired: str, *, force: bool = False) -> str:
    """Persist desired role; refuse silent switches unless force or confirmed."""
    desired_n = normalize_role(desired)
    if desired_n is None:
        print(f"ERROR: invalid role {desired!r}")
        sys.exit(1)

    existing = current_role()
    if existing is None:
        write_role(desired_n)
        return desired_n

    if existing == desired_n:
        if not ROLE_FILE.is_file() or read_role_file() != desired_n:
            write_role(desired_n)
        return desired_n

    print()
    print(f"WARNING: this machine is already configured as '{existing}'.")
    print(f"         You asked for '{desired_n}'.")
    print(f"  Role file: {ROLE_FILE}")
    print("  Env:       SCRAPEBOARD_ROLE (overrides file when set)")
    if force:
        write_role(desired_n)
        print(f"Role switched to '{desired_n}' (--force-role).")
        return desired_n
    if ASSUME_YES:
        print("Aborted under --yes (role mismatch). Pass --force-role to switch.")
        sys.exit(1)
    if not prompt_yes_no(
        f"Reconfigure this machine from '{existing}' to '{desired_n}'?",
        default=False,
    ):
        print("Aborted (role unchanged). Use --force-role to switch non-interactively.")
        sys.exit(1)
    write_role(desired_n)
    return desired_n


def git_available() -> bool:
    return shutil.which("git") is not None


def apply_sparse_checkout(role: str) -> bool:
    """Configure sparse-checkout for role. Returns False if git/sparse unavailable."""
    git_dir = ROOT / ".git"
    if not git_dir.exists():
        print("No .git directory — skip sparse-checkout (rsync/copy install).")
        return False
    if not git_available():
        print("git not found — skip sparse-checkout.")
        return False

    role_n = normalize_role(role)
    if role_n == "panel":
        patterns = ["/*", "!/worker/"]
        label = "panel (exclude worker/)"
    elif role_n == "worker":
        patterns = ["/*", "!/panel/", "!/deploy/"]
        label = "worker (exclude panel/ + deploy/)"
    else:
        print(f"ERROR: bad role for sparse-checkout: {role!r}")
        return False

    print(f"==> Sparse-checkout: {label}")
    init = subprocess.run(
        ["git", "-C", str(ROOT), "sparse-checkout", "init", "--no-cone"],
        capture_output=True,
        text=True,
    )
    if init.returncode != 0:
        print("    (warn) sparse-checkout init failed; will prune paths after pull")
        return False

    set_cmd = ["git", "-C", str(ROOT), "sparse-checkout", "set", "--no-cone", *patterns]
    if subprocess.run(set_cmd, capture_output=True, text=True).returncode == 0:
        note_installed(f"sparse-checkout ({role_n})")
        return True

    info = ROOT / ".git" / "info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "sparse-checkout").write_text("\n".join(patterns) + "\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(ROOT), "sparse-checkout", "reapply"],
        capture_output=True,
        text=True,
    )
    note_installed(f"sparse-checkout ({role_n})")
    return True


def prune_forbidden_paths(role: str) -> None:
    role_n = normalize_role(role)
    if role_n == "panel":
        victim = ROOT / "worker"
        if victim.exists():
            print(f"==> Removing {victim} (panel role)")
            shutil.rmtree(victim)
    elif role_n == "worker":
        for name in ("panel", "deploy"):
            victim = ROOT / name
            if victim.exists():
                print(f"==> Removing {victim} (worker role)")
                shutil.rmtree(victim)


# Printed so agents/timers can detect a no-op auto-update without restarting.
STATUS_ALREADY_UP_TO_DATE = "SCRAPEBOARD_STATUS=already_up_to_date"
STATUS_UPDATED = "SCRAPEBOARD_STATUS=updated"


def _git_rev_parse(rev: str) -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", rev],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    sha = (out or "").strip()
    return sha or None


def _git_current_branch() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    name = (out or "").strip()
    if not name or name == "HEAD":
        return None
    return name


def git_fetch_origin() -> int:
    print("==> git fetch origin --tags --prune")
    return subprocess.call(
        ["git", "-C", str(ROOT), "fetch", "origin", "--tags", "--prune"]
    )


def git_remote_tip(ref: str | None = None) -> str | None:
    """Resolve the remote SHA we would update to (after fetch)."""
    wanted = (ref if ref is not None else os.environ.get("SCRAPEBOARD_UPDATE_REF", "") or "").strip()
    if not wanted or wanted.lower() == "latest":
        branch = _git_current_branch()
        if branch:
            tip = _git_rev_parse(f"origin/{branch}")
            if tip:
                return tip
        # Fall back to upstream tracking ref
        tip = _git_rev_parse("@{u}")
        if tip:
            return tip
        tip = _git_rev_parse("origin/main") or _git_rev_parse("origin/master")
        return tip
    tip = _git_rev_parse(f"origin/{wanted}")
    if tip:
        return tip
    return _git_rev_parse(wanted)


def git_updates_available(ref: str | None = None) -> bool | None:
    """True if remote tip differs from HEAD, False if current, None if unknown."""
    if not (ROOT / ".git").exists() or not git_available():
        return None
    code = git_fetch_origin()
    if code != 0:
        return None
    head = _git_rev_parse("HEAD")
    tip = git_remote_tip(ref)
    if not head or not tip:
        return None
    return head != tip


def sync_repo_for_role(role: str, ref: str | None = None) -> int:
    """git pull/checkout with role sparse-checkout; prune forbidden trees.

    ref:
      None / "" / "latest" → git pull --ff-only on the current branch
      otherwise → fetch + checkout that branch/tag/SHA (worker sparse preserved)
    """
    role_n = normalize_role(role)
    if role_n is None:
        print("ERROR: sync requires role panel|worker")
        return 1
    if not (ROOT / ".git").exists():
        print("ERROR: not a git checkout — cannot update via git pull.")
        print("  Sync the tree manually, or clone with the correct role sparse-checkout.")
        prune_forbidden_paths(role_n)
        return 1
    if not git_available():
        print("ERROR: git not found on PATH")
        return 1

    apply_sparse_checkout(role_n)
    wanted = (ref if ref is not None else os.environ.get("SCRAPEBOARD_UPDATE_REF", "") or "").strip()
    if not wanted or wanted.lower() == "latest":
        # Prefer already-fetched tip when auto-update ran fetch first
        print("==> git pull --ff-only")
        code = subprocess.call(["git", "-C", str(ROOT), "pull", "--ff-only"])
        if code != 0:
            return code
    else:
        print("==> git fetch origin --tags --prune")
        code = subprocess.call(
            ["git", "-C", str(ROOT), "fetch", "origin", "--tags", "--prune"]
        )
        if code != 0:
            return code
        remote_ref = f"origin/{wanted}"
        has_remote = (
            subprocess.call(
                ["git", "-C", str(ROOT), "rev-parse", "--verify", remote_ref],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            == 0
        )
        if has_remote:
            print(f"==> git checkout -B {wanted} {remote_ref}")
            code = subprocess.call(
                ["git", "-C", str(ROOT), "checkout", "-B", wanted, remote_ref]
            )
        else:
            print(f"==> git checkout --force {wanted}")
            code = subprocess.call(
                ["git", "-C", str(ROOT), "checkout", "--force", wanted]
            )
        if code != 0:
            return code
    prune_forbidden_paths(role_n)
    print(f"==> Update sync complete (role={role_n})")
    return 0


def restart_worker_service(kind: str) -> int:
    """Restart the installed worker background service so a new VERSION is loaded.

    Returns 0 on success / best-effort; non-zero if restart commands failed.
    Skipped when SCRAPEBOARD_SKIP_SERVICE_RESTART=1 (set by agent remote-update
    path — the agent exits and KeepAlive/systemd restarts instead).
    """
    if env_truthy("SCRAPEBOARD_SKIP_SERVICE_RESTART"):
        print("==> Skipping service restart (SCRAPEBOARD_SKIP_SERVICE_RESTART=1)")
        print("    Agent will exit; systemd / LaunchAgent / schtasks KeepAlive reloads new code.")
        return 0

    print()
    print("==> Restarting worker service so the new agent VERSION is loaded…")
    code = 1
    if kind == "windows":
        # End then Run the scheduled task created by install_service.bat
        end = subprocess.call(
            ["schtasks", "/End", "/TN", "ScrapeboardWorker"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        run_c = subprocess.call(
            ["schtasks", "/Run", "/TN", "ScrapeboardWorker"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        code = 0 if run_c == 0 else max(end, run_c)
        if code != 0:
            print("  schtasks restart failed — is the service installed?")
            print("  Install/restart:  worker\\install_service.bat")
            print("  Or manually:      schtasks /End /TN ScrapeboardWorker")
            print("                    schtasks /Run /TN ScrapeboardWorker")
    elif kind == "macos":
        uid = os.getuid()
        label = f"gui/{uid}/com.scrapeboard.worker"
        code = subprocess.call(
            ["launchctl", "kickstart", "-k", label],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if code != 0:
            print(f"  launchctl kickstart failed ({label})")
            print("  Install/restart:  bash worker/install_service.sh")
            print(f"  Or manually:      launchctl kickstart -k {label}")
    else:
        # Linux systemd user unit
        code = subprocess.call(
            ["systemctl", "--user", "restart", "scrapeboard-worker"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if code != 0:
            print("  systemctl --user restart scrapeboard-worker failed")
            print("  Install/restart:  bash worker/install_service.sh")
            print("  Or manually:      systemctl --user restart scrapeboard-worker")

    if code == 0:
        note_installed("worker service restarted")
        print("  Service restart requested OK.")
    else:
        note_manual("Restart the worker service so it loads the new VERSION (see commands above)")
    print_worker_restart_verify_commands(kind)
    return 0  # update itself succeeded; restart is best-effort


def print_worker_restart_verify_commands(kind: str) -> None:
    """Exact copy-paste commands to confirm VERSION and that the process is new."""
    worker = ROOT / "worker"
    print()
    print("Verify on this machine:")
    print(f"  cd {worker}")
    print("  grep VERSION agent.py")
    if kind == "windows":
        print("  schtasks /Query /TN ScrapeboardWorker /V /FO LIST")
        print("  findstr /C:\"scrapeboard worker v\" logs\\worker.log")
    elif kind == "macos":
        print(f"  launchctl print gui/$(id -u)/com.scrapeboard.worker | head")
        print("  grep -E 'scrapeboard worker v' logs/worker.log | tail -3")
    else:
        print("  systemctl --user status scrapeboard-worker --no-pager")
        print("  journalctl --user -u scrapeboard-worker -n 20 --no-pager")
        print("  grep -E 'scrapeboard worker v' logs/worker.log | tail -3")
    print("Panel should show agent version 0.8.1+ after the next heartbeat (~15s).")
    print("Linux: systemctl --user restart scrapeboard-worker  # required after pull if still on 0.7.0")


def run_auto_update_mode(role: str, kind: str, ref: str | None = None) -> int:
    """Daily/timer path: fetch, skip when current, otherwise update + install + restart."""
    global ASSUME_YES
    print()
    print(f"Auto-update check (role={role})")
    if ref and str(ref).strip() and str(ref).strip().lower() != "latest":
        print(f"Git ref: {ref}")
    print("-" * 40)

    if role == "panel" and kind == "linux" and hestia_detected() and is_root():
        # Production panel: use the Hestia auto-update script (build + systemd).
        script = ROOT / "deploy" / "hestiacp" / "auto_update.sh"
        if script.is_file():
            return run(["bash", str(script)])
        print("ERROR: deploy/hestiacp/auto_update.sh missing")
        return 1

    avail = git_updates_available(ref)
    if avail is False:
        print("Already up to date — skipped install/restart.")
        print(STATUS_ALREADY_UP_TO_DATE)
        return 0
    if avail is None:
        print("Could not determine remote tip — running update anyway.")
    else:
        print("Updates available — pulling, installing, and restarting.")

    ASSUME_YES = True
    code = run_update_mode(role, kind, ref=ref)
    if code == 0:
        print(STATUS_UPDATED)
    return code


def run_update_mode(role: str, kind: str, ref: str | None = None) -> int:
    """Honor persisted role: sync sparse tree, then role-specific refresh hints."""
    print()
    print(f"Update mode (role={role})")
    if ref and str(ref).strip() and str(ref).strip().lower() != "latest":
        print(f"Git ref: {ref}")
    print("-" * 40)
    code = sync_repo_for_role(role, ref=ref)
    if code != 0:
        return code

    if role == "panel":
        if kind == "linux" and hestia_detected():
            print()
            print("Next: as root, rebuild/restart the panel:")
            print("  bash deploy/hestiacp/update.sh")
            print("(That script also re-asserts role=panel and panel sparse-checkout.)")
            if is_root() and prompt_yes_no(
                "Run deploy/hestiacp/update.sh now?",
                default=ASSUME_YES,
            ):
                return run(["bash", str(ROOT / "deploy" / "hestiacp" / "update.sh")])
        else:
            print()
            print("Local panel: refresh backend deps / rebuild frontend as needed.")
            backend = ROOT / "panel" / "backend"
            pip = ensure_venv(sys.executable, backend / ".venv")
            if pip:
                pip_install_requirements(pip, backend / "requirements.txt", cwd=backend)
            install_frontend_deps(kind)
        print_install_summary()
        return 0

    if not path_exists("worker"):
        print("ERROR: worker/ missing after sync — sparse-checkout may have failed.")
        return 1
    worker = ROOT / "worker"
    req = worker / "requirements.txt"
    print()
    print("Refreshing worker Python deps…")
    venv = worker / ".venv"
    if kind == "windows":
        pip = venv / "Scripts" / "pip.exe"
    else:
        pip = venv / "bin" / "pip"
    if pip.is_file() and req.is_file():
        code = run([str(pip), "install", "-r", str(req)], cwd=worker)
        if code != 0:
            return code
        note_installed("worker pip requirements refreshed")
    else:
        print(f"  (skip pip: no venv at {venv} — run setup_and_run first)")
        note_manual("Run worker setup_and_run to create .venv")

    # Show VERSION on disk so operators can confirm the pull landed.
    agent_py = worker / "agent.py"
    if agent_py.is_file():
        try:
            for line in agent_py.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("VERSION"):
                    print(f"==> On-disk {line.strip()}")
                    break
        except OSError:
            pass

    # Manual --update must restart the service; agent remote-update skips via env.
    do_restart = ASSUME_YES or prompt_yes_no(
        "Restart the worker service now so the panel sees the new VERSION?",
        default=True,
    )
    if do_restart:
        restart_worker_service(kind)
    else:
        print()
        print("Skipped service restart — panel will keep showing the old VERSION until you restart.")
        print_worker_restart_verify_commands(kind)
        note_manual("Restart worker service after update (see verify commands above)")

    print("Config/token stay in worker/worker_config.json.")
    print_install_summary()
    return 0


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
    note_installed("deploy/config.env (from example)")
    if ASSUME_YES:
        note_manual("Edit deploy/config.env (BOOTSTRAP_ADMIN_PASSWORD, DOMAIN) then re-run")
        print("(--yes) continuing with example config — change password after first login.")
        return cfg
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
        note_manual(f"sudo bash {script}")
        return 1
    cfg = ensure_deploy_config()
    if ASSUME_YES and not cfg.is_file():
        print("ERROR: deploy/config.env required for noninteractive Hestia install.")
        return 1
    note_manual("Hestia domain must exist (WEB → Add Web Domain + SSL) before install succeeds")
    note_manual("DNS A/AAAA for DOMAIN must point at this VPS")
    return run(["bash", str(script)])


def prepare_local_backend() -> Path:
    backend = ROOT / "panel" / "backend"
    env_file = backend / ".env"
    example = backend / ".env.example"
    write_local_backend_env(env_file, example)
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


def setup_local_panel_deps(kind: str) -> int:
    """Create backend venv + deps + frontend node modules. Idempotent."""
    backend = prepare_local_backend()
    pip = ensure_venv(sys.executable, backend / ".venv")
    if not pip:
        return 1
    if not pip_install_requirements(pip, backend / "requirements.txt", cwd=backend):
        return 1
    install_frontend_deps(kind)
    return 0


def run_local_panel(kind: str) -> int:
    print()
    print("Local control panel (development)")
    print("  HestiaCP / production systemd is Linux-server only.")
    print("  This path prepares the FastAPI API and points you at the React UI.")
    print()

    code = setup_local_panel_deps(kind)
    if code != 0:
        print_install_summary()
        return code

    backend = ROOT / "panel" / "backend"
    print_local_frontend_steps()

    start_default = not ASSUME_YES
    if kind in ("macos", "linux"):
        run_sh = ROOT / "panel" / "run.sh"
        if not run_sh.is_file():
            print(f"ERROR: missing {run_sh}")
            return 1
        if prompt_yes_no("Start local API now (panel/run.sh --reload)?", default=start_default):
            print_install_summary()
            exec_replace(["bash", str(run_sh), "--reload"])
        print()
        print("Start later with:")
        print(f"  bash {run_sh} --reload")
        print_install_summary()
        return 0

    # Windows
    uvicorn = backend / ".venv" / "Scripts" / "uvicorn.exe"
    print("Start API later with:")
    print(f'  "{uvicorn}" app.main:app --reload --host 127.0.0.1 --port 3010')
    print(f"  (from {backend})")
    if prompt_yes_no("Start local API now?", default=start_default):
        print_install_summary()
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
    print_install_summary()
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
        if ASSUME_YES:
            cfg = ROOT / "deploy" / "config.env"
            if hestia and (cfg.is_file() or default == "1"):
                default = "1"
            else:
                default = "2"
                if not hestia:
                    print("(--yes) Hestia not detected — using local panel path.")
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
            code = run_hestia_install()
            print_install_summary()
            return code
        if choice == "3":
            print_hestia_guide()
            return 0
        return run_local_panel(kind)

    # macOS / Windows / other — no Hestia
    print(f"{os_label(kind)} cannot run the HestiaCP production installer.")
    print("Use a Linux VPS with Hestia for production (deploy/hestiacp/README.md).")
    print("Here you can set up a local development panel.")
    if ASSUME_YES:
        print("(--yes) auto local panel path.")
        return run_local_panel(kind)
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
    print("Note: panel role writes .scrapeboard-role=panel; sparse-checkout excludes worker/.")
    print("Updates: bash deploy/hestiacp/update.sh (refuses if role=worker).")


# ── Worker ─────────────────────────────────────────────────────────────────


def worker_setup_cmd(kind: str) -> list[str] | None:
    worker = ROOT / "worker"
    if kind == "windows":
        bat = worker / "setup_and_run.bat"
        if not bat.is_file():
            return None
        cmd = ["cmd", "/c", str(bat)]
        if ASSUME_YES:
            cmd.append("/Y")
        if WANT_TAILSCALE:
            cmd.append("--tailscale")
        return cmd
    if kind in ("macos", "linux"):
        sh = worker / "setup_and_run.sh"
        if sh.is_file():
            cmd = ["bash", str(sh)]
            if ASSUME_YES:
                cmd.append("--yes")
            if WANT_TAILSCALE:
                cmd.append("--tailscale")
            return cmd
        return None
    return None


def worker_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ASSUME_YES:
        env["SCRAPEBOARD_ASSUME_YES"] = "1"
    if WANT_TAILSCALE:
        env["SCRAPEBOARD_TAILSCALE"] = "1"
    return env


def linux_venv_package_hint() -> str | None:
    """If ensurepip is missing on Linux, return the apt one-liner; else None."""
    if platform.system().lower() != "linux":
        return None
    try:
        import ensurepip  # noqa: F401
    except ImportError:
        ver = f"{sys.version_info.major}.{sys.version_info.minor}"
        return f"sudo apt-get update && sudo apt-get install -y python{ver}-venv"
    return None


def warn_linux_venv_prereq() -> None:
    """Light preflight before handing off to worker/setup_and_run.sh."""
    hint = linux_venv_package_hint()
    if not hint:
        return
    print("Note: this Python build is missing ensurepip/venv support.")
    print("  setup_and_run.sh will try to install it automatically if you are root")
    print("  or have passwordless sudo; otherwise run:")
    print(f"  {hint}")
    print()


def worker_menu(kind: str) -> int:
    print()
    print("Worker agent setup")
    print("-" * 40)
    print("Runs worker/setup_and_run.* — venv, deps, selftest, wizard (panel URL + token).")
    print("Optional Tailscale: pass --tailscale or set SCRAPEBOARD_TAILSCALE=1 (no forced login).")
    print("After setup, background service installs automatically with --yes when config exists.")
    print()
    print("Noninteractive wizard needs:")
    print("  SCRAPEBOARD_PANEL_URL   SCRAPEBOARD_TOKEN")
    print("  (or an existing worker/worker_config.json)")
    print()

    if not path_exists("worker"):
        print("ERROR: worker/ folder not found.")
        print("This checkout may be panel-only (sparse-checkout).")
        print("On the scrape machine: clone the repo, then:")
        print("  python3 install.py --role worker --yes")
        return 1

    cmd = worker_setup_cmd(kind)
    if not cmd:
        print("ERROR: no setup_and_run script for this OS under worker/")
        return 1

    if kind == "linux":
        warn_linux_venv_prereq()

    if ASSUME_YES:
        cfg = ROOT / "worker" / "worker_config.json"
        token = (os.environ.get("SCRAPEBOARD_TOKEN") or "").strip()
        if not cfg.is_file() and not token:
            print("ERROR: --yes worker setup needs SCRAPEBOARD_TOKEN (and usually SCRAPEBOARD_PANEL_URL)")
            print("  or an existing worker/worker_config.json")
            note_manual("Set SCRAPEBOARD_PANEL_URL + SCRAPEBOARD_TOKEN for noninteractive wizard")
            print_install_summary()
            return 1
        if not token and not cfg.is_file():
            pass
        note_manual("If Tailscale enabled: run `tailscale up` once for login")

    if not prompt_yes_no("Start worker setup now?", default=True):
        print("Cancelled.")
        return 0

    env = worker_env()
    if kind == "windows":
        code = run(cmd, cwd=ROOT / "worker", env=env or None)
        print_install_summary()
        return code
    # Unix: merge env then exec
    if env:
        os.environ.update(env)
    exec_replace(cmd, cwd=ROOT / "worker")
    return 0  # unreachable on Unix after exec


# ── Main ───────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="install.py",
        description="Scrapeboard installer — control panel or worker (interactive or --yes).",
        epilog=(
            "Machine role is stored in .scrapeboard-role (gitignored) and honored on "
            "every update. Override with SCRAPEBOARD_ROLE=panel|worker.\n"
            "Noninteractive: --role panel|worker --yes  (or SCRAPEBOARD_ASSUME_YES=1).\n"
            "Worker wizard credentials: SCRAPEBOARD_PANEL_URL + SCRAPEBOARD_TOKEN "
            "(or existing worker_config.json).\n"
            "Tailscale: --tailscale or SCRAPEBOARD_TAILSCALE=1 (install only; login still manual).\n"
            "Panel sparse-checkout excludes worker/; worker excludes panel/ and deploy/."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--role",
        choices=("panel", "worker"),
        help="Skip the role menu (panel|worker). Persists to .scrapeboard-role.",
    )
    p.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Noninteractive defaults after role is set (also SCRAPEBOARD_ASSUME_YES=1).",
    )
    p.add_argument(
        "--tailscale",
        action="store_true",
        help="Enable Tailscale for worker setup (best-effort install; do not force login).",
    )
    p.add_argument(
        "--update",
        action="store_true",
        help="Sync this checkout for the machine role (sparse git pull), then refresh deps hints.",
    )
    p.add_argument(
        "--auto-update",
        action="store_true",
        help="Check git for updates; if behind, pull + install deps + restart (for daily timers).",
    )
    p.add_argument(
        "--ref",
        default="",
        help="With --update/--auto-update: git branch/tag/SHA (or 'latest' for current-branch pull). "
        "Also SCRAPEBOARD_UPDATE_REF.",
    )
    p.add_argument(
        "--force-role",
        action="store_true",
        help="Allow switching .scrapeboard-role without an interactive confirm.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print detected OS, role, and planned paths, then exit.",
    )
    return p.parse_args(argv)


def dry_run(kind: str) -> int:
    print(f"OS: {os_label(kind)} ({platform.platform()})")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Root: {ROOT}")
    print(f"Assume yes: {ASSUME_YES}")
    print(f"Tailscale flag: {WANT_TAILSCALE}")
    print(f"Hestia detected: {hestia_detected() if kind == 'linux' else 'n/a (not Linux)'}")
    role = current_role()
    print(f"Machine role: {role or '(unset)'}  [{ROLE_FILE.name}; env SCRAPEBOARD_ROLE]")
    print()
    print("Panel sparse: /*  !/worker/")
    print("Worker sparse: /*  !/panel/  !/deploy/")
    print()
    print("Panel paths:")
    if kind == "linux":
        print("  production → bash deploy/hestiacp/install.sh (root)")
        print("  update     → bash deploy/hestiacp/update.sh (requires role=panel)")
    else:
        print("  production → not available here; use Linux + Hestia")
    print("  local      → venv + deps + frontend npm/bun; panel/run.sh (Unix)")
    print()
    print("Worker paths:")
    cmd = worker_setup_cmd(kind)
    print(f"  setup      → {' '.join(cmd) if cmd else '(missing worker/)'}")
    print("  update     → python3 install.py --role worker --update")
    print("            → bash worker/update.sh   (Windows: worker\\update.bat)")
    if kind == "windows":
        print("  service    → worker/install_service.bat")
    else:
        print("  service    → bash worker/install_service.sh")
    print()
    print("Fully automatic examples:")
    print("  python3 install.py --role worker --yes")
    print("  SCRAPEBOARD_PANEL_URL=https://scrape.example SCRAPEBOARD_TOKEN=… \\")
    print("    python3 install.py --role worker --yes")
    print("  python3 install.py --role panel --yes   # local on macOS/Windows; Hestia if Linux+config")
    return 0


def main(argv: list[str] | None = None) -> int:
    global ASSUME_YES, WANT_TAILSCALE

    ensure_python()
    args = parse_args(argv)
    ASSUME_YES = bool(args.yes) or env_truthy("SCRAPEBOARD_ASSUME_YES")
    WANT_TAILSCALE = bool(args.tailscale) or env_truthy("SCRAPEBOARD_TAILSCALE")
    if ASSUME_YES:
        os.environ["SCRAPEBOARD_ASSUME_YES"] = "1"
    if WANT_TAILSCALE:
        os.environ["SCRAPEBOARD_TAILSCALE"] = "1"

    kind = detect_os()

    if args.dry_run:
        return dry_run(kind)

    banner(kind)
    persisted = current_role()
    if persisted:
        print(f" Configured role: {persisted} ({ROLE_FILE.name})")
        if env_role():
            print(f" Env override:    SCRAPEBOARD_ROLE={env_role()}")
        print()

    if kind == "other":
        print(f"Unsupported OS: {platform.system()}")
        print("Supported: macOS, Linux, Windows.")
        return 1

    if args.update or args.auto_update:
        role = args.role or current_role()
        if not role:
            which = "--auto-update" if args.auto_update else "--update"
            print(f"ERROR: {which} needs a role. Pass --role panel|worker or set .scrapeboard-role.")
            return 1
        role = ensure_role(role, force=args.force_role)
        ref = (args.ref or os.environ.get("SCRAPEBOARD_UPDATE_REF") or "").strip() or None
        if args.auto_update:
            return run_auto_update_mode(role, kind, ref=ref)
        return run_update_mode(role, kind, ref=ref)

    role = args.role
    if not role:
        if ASSUME_YES:
            role = persisted
            if not role:
                print("ERROR: --yes requires --role panel|worker (or an existing .scrapeboard-role).")
                return 1
            print(f"(--yes) using role={role}")
        else:
            default_pick = "1" if kind == "linux" and hestia_detected() else "2"
            if persisted == "panel":
                default_pick = "1"
            elif persisted == "worker":
                default_pick = "2"
            print("What do you want to set up on this machine?")
            print("(Choice is saved to .scrapeboard-role and used for later updates.)")
            pick = choose(
                [
                    ("1", "Control panel (Hestia production or local API/UI)"),
                    ("2", "Worker agent (scrape machine)"),
                    ("q", "Quit"),
                ],
                default=default_pick,
            )
            if pick == "q":
                print("Bye.")
                return 0
            role = "panel" if pick == "1" else "worker"

    role = ensure_role(role, force=args.force_role)

    if role == "panel":
        # With --yes on Hestia Linux: apply sparse. Local/dev: keep full tree unless asked.
        sparse_default = bool(ASSUME_YES and kind == "linux" and hestia_detected())
        if (ROOT / ".git").is_dir() and prompt_yes_no(
            "Apply panel sparse-checkout now (exclude worker/ from this clone)?",
            default=sparse_default,
        ):
            apply_sparse_checkout("panel")
            prune_forbidden_paths("panel")
        return panel_menu(kind)

    # worker — dedicated scrape hosts should not keep panel/deploy sources
    if (ROOT / ".git").is_dir() and prompt_yes_no(
        "Apply worker sparse-checkout now (exclude panel/ and deploy/ from this clone)?",
        default=True,
    ):
        apply_sparse_checkout("worker")
        prune_forbidden_paths("worker")
    return worker_menu(kind)


if __name__ == "__main__":
    sys.exit(main())
