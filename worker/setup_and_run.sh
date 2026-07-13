#!/usr/bin/env bash
# Scrapeboard worker — Linux / macOS first-run setup + selftest + start
# Usage:  bash setup_and_run.sh
#         bash setup_and_run.sh --yes          # noninteractive defaults
#         SCRAPEBOARD_ASSUME_YES=1 bash setup_and_run.sh
# Env (wizard, with --yes): SCRAPEBOARD_PANEL_URL SCRAPEBOARD_TOKEN
# Optional: SCRAPEBOARD_TAILSCALE=1  SCRAPEBOARD_WORKER_NAME  SCRAPEBOARD_ENGINE
set -u
cd "$(dirname "$0")" || exit 1

ASSUME_YES=0
WANT_TAILSCALE=0
for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    --tailscale) WANT_TAILSCALE=1 ;;
    -h|--help)
      echo "Usage: bash setup_and_run.sh [--yes|-y] [--tailscale]"
      echo "  --yes         Noninteractive: install deps, wizard via env, auto service"
      echo "  --tailscale   Enable Tailscale in config (best-effort install; login still manual)"
      exit 0
      ;;
  esac
done

case "${SCRAPEBOARD_ASSUME_YES:-}" in
  1|true|TRUE|yes|YES|y|Y|on|ON) ASSUME_YES=1 ;;
esac
case "${SCRAPEBOARD_TAILSCALE:-}" in
  1|true|TRUE|yes|YES|y|Y|on|ON) WANT_TAILSCALE=1 ;;
esac

if [[ "$ASSUME_YES" -eq 1 ]]; then
  export SCRAPEBOARD_ASSUME_YES=1
fi
if [[ "$WANT_TAILSCALE" -eq 1 ]]; then
  export SCRAPEBOARD_TAILSCALE=1
fi

INSTALLED=()
MANUAL=()
note_installed() { INSTALLED+=("$1"); }
note_manual() { MANUAL+=("$1"); }

echo "================================================================"
echo " Scrapeboard Worker — setup ($(uname -s))"
echo "================================================================"
echo "Working dir: $(pwd)"
[[ "$ASSUME_YES" -eq 1 ]] && echo "Mode: noninteractive (--yes / SCRAPEBOARD_ASSUME_YES=1)"
echo

OS="$(uname -s)"

# ── Privilege helpers ───────────────────────────────────────────────────────

can_elevate() {
  if [[ "$(id -u)" -eq 0 ]]; then
    return 0
  fi
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

run_as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo -n "$@"
  fi
}

py_mm() {
  python3 -c 'import sys; print("%d.%d" % (sys.version_info[:2]))' 2>/dev/null || echo "3"
}

ensurepip_ok() {
  python3 -c "import ensurepip" >/dev/null 2>&1
}

python_version_ok() {
  python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null
}

# ── Linux package install (best-effort, noninteractive) ─────────────────────

apt_update_once() {
  if [[ "${_APT_UPDATED:-0}" -eq 1 ]]; then
    return 0
  fi
  run_as_root env DEBIAN_FRONTEND=noninteractive apt-get update -qq || return 1
  _APT_UPDATED=1
}

linux_apt_install() {
  apt_update_once || return 1
  run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
}

print_venv_install_hint() {
  local mm pkg
  mm="$(py_mm)"
  pkg="python${mm}-venv"
  if [[ -f /etc/debian_version ]] || command -v apt-get >/dev/null 2>&1; then
    echo "ERROR: Python venv/ensurepip is not available."
    echo "Install the venv package, then re-run this script:"
    echo "  sudo apt-get update && sudo apt-get install -y ${pkg}"
    echo "(or: sudo apt-get install -y python3-venv)"
  elif command -v dnf >/dev/null 2>&1; then
    echo "ERROR: Python venv/ensurepip is not available."
    echo "Try:  sudo dnf install -y python3"
  elif command -v yum >/dev/null 2>&1; then
    echo "ERROR: Python venv/ensurepip is not available."
    echo "Try:  sudo yum install -y python3"
  else
    echo "ERROR: Python venv/ensurepip is not available on this system."
    echo "Install your distro's python3 venv / ensurepip package, then re-run."
  fi
  note_manual "Install python3-venv / ensurepip for your distro"
}

try_install_venv_pkg() {
  local pkg
  if [[ -f /etc/debian_version ]] || command -v apt-get >/dev/null 2>&1; then
    pkg="python$(py_mm)-venv"
    echo "Installing ${pkg} (needed for python3 -m venv)…"
    if linux_apt_install "${pkg}"; then
      note_installed "${pkg}"
      return 0
    fi
    return 1
  fi
  if command -v dnf >/dev/null 2>&1; then
    echo "Installing python3 (provides venv/ensurepip)…"
    run_as_root dnf install -y python3 && note_installed "python3 (dnf)" && return 0
    return 1
  fi
  if command -v yum >/dev/null 2>&1; then
    echo "Installing python3 (provides venv/ensurepip)…"
    run_as_root yum install -y python3 && note_installed "python3 (yum)" && return 0
    return 1
  fi
  return 1
}

ensure_linux_python() {
  local need_pkgs=()
  if ! command -v python3 >/dev/null 2>&1; then
    need_pkgs+=(python3)
  fi
  if ! command -v pip3 >/dev/null 2>&1 && ! python3 -m pip --version >/dev/null 2>&1; then
    need_pkgs+=(python3-pip)
  fi
  if ! ensurepip_ok; then
    need_pkgs+=("python$(py_mm)-venv")
  fi
  if [[ ${#need_pkgs[@]} -eq 0 ]]; then
    return 0
  fi
  echo "Missing system packages: ${need_pkgs[*]}"
  if ! can_elevate; then
    echo "ERROR: need root or passwordless sudo to install: ${need_pkgs[*]}"
    note_manual "sudo apt-get install -y ${need_pkgs[*]}"
    return 1
  fi
  if [[ -f /etc/debian_version ]] || command -v apt-get >/dev/null 2>&1; then
    if linux_apt_install "${need_pkgs[@]}"; then
      note_installed "${need_pkgs[*]}"
      return 0
    fi
    return 1
  fi
  if command -v dnf >/dev/null 2>&1; then
    run_as_root dnf install -y python3 python3-pip && note_installed "python3 python3-pip (dnf)" && return 0
  fi
  if command -v yum >/dev/null 2>&1; then
    run_as_root yum install -y python3 python3-pip && note_installed "python3 python3-pip (yum)" && return 0
  fi
  note_manual "Install python3 + pip for your distro"
  return 1
}

ensure_linux_build_essentials_if_needed() {
  # Only if a compiler is missing — many wheels need it for occasional native deps.
  if command -v gcc >/dev/null 2>&1 && command -v g++ >/dev/null 2>&1; then
    return 0
  fi
  if ! can_elevate; then
    note_manual "Optional: install build-essential if pip fails compiling native wheels"
    return 0
  fi
  if [[ -f /etc/debian_version ]] || command -v apt-get >/dev/null 2>&1; then
    echo "Installing build-essential (native wheels may need a compiler)…"
    if linux_apt_install build-essential; then
      note_installed "build-essential"
    fi
  fi
}

# ── macOS: Homebrew + Python ────────────────────────────────────────────────

print_brew_install_url() {
  echo "Homebrew is not installed."
  echo "Install from: https://brew.sh"
  echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
  note_manual "Install Homebrew from https://brew.sh (not auto-run unless you confirm)"
}

ensure_macos_python() {
  if command -v python3 >/dev/null 2>&1 && python_version_ok; then
    return 0
  fi

  local brew=""
  if command -v brew >/dev/null 2>&1; then
    brew="$(command -v brew)"
  elif [[ -x /opt/homebrew/bin/brew ]]; then
    brew="/opt/homebrew/bin/brew"
  elif [[ -x /usr/local/bin/brew ]]; then
    brew="/usr/local/bin/brew"
  fi

  if [[ -z "$brew" ]]; then
    print_brew_install_url
    if [[ "$ASSUME_YES" -eq 1 ]]; then
      echo "ERROR: --yes cannot silently install Homebrew. Install brew, then re-run."
      return 1
    fi
    if [[ -t 0 ]]; then
      read -r -p "Open brew install instructions and exit so you can install it? [Y/n] " ans || ans=""
      if [[ ! "${ans:-}" =~ ^[Nn] ]]; then
        echo "After installing Homebrew, re-run:  bash setup_and_run.sh"
        return 1
      fi
    fi
    echo "ERROR: python3 3.10+ not found and Homebrew is missing."
    return 1
  fi

  echo "Installing python@3.12 via Homebrew…"
  if "$brew" install python@3.12; then
    note_installed "python@3.12 (Homebrew)"
    # Prefer brew python on PATH for this session
    if [[ -x /opt/homebrew/opt/python@3.12/bin/python3 ]]; then
      export PATH="/opt/homebrew/opt/python@3.12/bin:$PATH"
    elif [[ -x /usr/local/opt/python@3.12/bin/python3 ]]; then
      export PATH="/usr/local/opt/python@3.12/bin:$PATH"
    fi
  else
    echo "brew install python@3.12 failed"
    note_manual "brew install python@3.12"
    return 1
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 still not on PATH after Homebrew install."
    note_manual "Add Homebrew python to PATH, then re-run"
    return 1
  fi
  return 0
}

# ── Ensure Python present ───────────────────────────────────────────────────

ensure_python() {
  if [[ "$OS" == "Darwin" ]]; then
    ensure_macos_python || return 1
  elif [[ "$OS" == "Linux" ]]; then
    if ! command -v python3 >/dev/null 2>&1 || ! ensurepip_ok; then
      ensure_linux_python || return 1
    fi
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found. Install Python 3.10+."
    note_manual "Install Python 3.10+"
    return 1
  fi
  if ! python_version_ok; then
    echo "ERROR: Python 3.10+ required (found $(python3 --version 2>&1))."
    if [[ "$OS" == "Darwin" ]]; then
      note_manual "brew install python@3.12"
    else
      note_manual "Install python3 >= 3.10"
    fi
    return 1
  fi
  echo "Python: $(python3 --version)"
  return 0
}

cleanup_broken_venv() {
  if [[ -d .venv ]]; then
    echo "Removing incomplete .venv so the next run can retry cleanly…"
    rm -rf .venv
  fi
}

create_venv() {
  local err
  err="$(mktemp)"
  if python3 -m venv .venv >"$err" 2>&1; then
    rm -f "$err"
    return 0
  fi
  cat "$err" >&2
  rm -f "$err"
  cleanup_broken_venv
  return 1
}

venv_looks_broken() {
  [[ -d .venv ]] && [[ ! -x .venv/bin/python ]]
}

ensure_worker_venv() {
  if [[ -x .venv/bin/python ]]; then
    echo "Using existing .venv"
    return 0
  fi

  if venv_looks_broken; then
    cleanup_broken_venv
  fi

  if [[ "$OS" == "Linux" ]] && ! ensurepip_ok; then
    echo "Python ensurepip/venv support is missing."
    if can_elevate; then
      if ! try_install_venv_pkg; then
        print_venv_install_hint
        exit 1
      fi
      if ! ensurepip_ok; then
        print_venv_install_hint
        exit 1
      fi
    else
      print_venv_install_hint
      exit 1
    fi
  fi

  echo "--- Creating virtual environment (.venv) ---"
  if create_venv; then
    note_installed "worker/.venv"
    return 0
  fi

  if [[ "$OS" == "Linux" ]]; then
    if can_elevate; then
      echo "venv create failed; attempting to install the system venv package…"
      if try_install_venv_pkg && create_venv; then
        note_installed "worker/.venv"
        return 0
      fi
    fi
    print_venv_install_hint
    exit 1
  fi

  echo "ERROR: failed to create .venv"
  exit 1
}

print_summary() {
  echo
  echo "================================================================"
  echo " Setup summary"
  echo "================================================================"
  if [[ ${#INSTALLED[@]} -gt 0 ]]; then
    echo "Installed / prepared:"
    for x in "${INSTALLED[@]}"; do echo "  - $x"; done
  else
    echo "Installed / prepared: (nothing new — already satisfied)"
  fi
  if [[ ${#MANUAL[@]} -gt 0 ]]; then
    echo "Still needs manual input / action:"
    for x in "${MANUAL[@]}"; do echo "  - $x"; done
  else
    echo "Still needs manual input / action: (none from this run)"
  fi
  if [[ ! -f worker_config.json ]]; then
    echo "  - Panel URL + worker token (wizard), or set:"
    echo "      SCRAPEBOARD_PANEL_URL  SCRAPEBOARD_TOKEN"
  fi
  if [[ "$WANT_TAILSCALE" -eq 1 ]] || [[ -f worker_config.json ]]; then
    if python3 -c "import json; c=json.load(open('worker_config.json')); raise SystemExit(0 if c.get('tailscale_enabled') else 1)" 2>/dev/null; then
      echo "  - Tailscale login if not already up:  tailscale up"
    fi
  fi
  echo "================================================================"
}

# ── Main flow ───────────────────────────────────────────────────────────────

ensure_python || { print_summary; exit 1; }

if [[ "$OS" == "Linux" ]]; then
  ensure_linux_build_essentials_if_needed || true
fi

ensure_worker_venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "--- Installing requirements ---"
python -m pip install --upgrade pip
if ! python -m pip install --upgrade -r requirements.txt; then
  echo "pip install failed"
  if [[ "$OS" == "Linux" ]] && can_elevate; then
    echo "Retrying after ensuring build-essential…"
    ensure_linux_build_essentials_if_needed || true
    if ! python -m pip install --upgrade -r requirements.txt; then
      echo "pip install failed again"
      note_manual "Fix pip errors, then re-run setup_and_run.sh"
      print_summary
      exit 1
    fi
  else
    note_manual "Fix pip errors, then re-run setup_and_run.sh"
    print_summary
    exit 1
  fi
fi
note_installed "pip packages from requirements.txt"

echo "--- Playwright Chromium (first run) ---"
python -m playwright install chromium || true
# Linux system libs (best-effort; playwright may prompt for sudo)
if [[ "$OS" == "Linux" ]]; then
  python -m playwright install-deps chromium || true
fi
note_installed "Playwright Chromium (best-effort)"

echo
echo "--- Selftest (chrome) ---"
python agent.py --selftest --engine chrome
echo "selftest exit: $?"

if [[ ! -f worker_config.json ]]; then
  echo
  echo "--- First-run wizard (creates worker_config.json) ---"
  if [[ "$ASSUME_YES" -eq 1 ]] && [[ -z "${SCRAPEBOARD_TOKEN:-}" ]]; then
    echo "ERROR: noninteractive setup requires SCRAPEBOARD_TOKEN (and usually SCRAPEBOARD_PANEL_URL)."
    note_manual "export SCRAPEBOARD_PANEL_URL=… SCRAPEBOARD_TOKEN=… then re-run with --yes"
    print_summary
    exit 1
  fi
  if ! python -c "from agent import bootstrap_agent_deps, run_setup_wizard; bootstrap_agent_deps(); run_setup_wizard()"; then
    print_summary
    exit 1
  fi
  note_installed "worker_config.json"
fi

SERVICE_DONE=0
if [[ -f worker_config.json ]]; then
  echo
  echo "================================================================"
  echo " Background service (recommended)"
  echo " Starts at login, keeps running after this terminal closes,"
  echo " and waits for panel jobs."
  echo "================================================================"
  echo "  Install:   bash install_service.sh"
  echo "  Uninstall: bash install_service.sh --uninstall"
  echo "  Logs:      logs/worker.log"
  echo

  do_svc=0
  if [[ "$ASSUME_YES" -eq 1 ]]; then
    echo "Noninteractive: installing background service…"
    do_svc=1
  elif [[ -t 0 ]]; then
    read -r -p "Install background service now? [y/N] " ans || ans=""
    if [[ "${ans:-}" =~ ^[Yy] ]]; then
      do_svc=1
    fi
  else
    echo "Non-interactive shell — skip prompt. To install later:  bash install_service.sh"
    note_manual "bash install_service.sh  (background service)"
  fi

  if [[ "$do_svc" -eq 1 ]]; then
    if bash install_service.sh; then
      note_installed "background service (install_service.sh)"
      SERVICE_DONE=1
      echo
      echo "Service installed. Tail logs with:  tail -f logs/worker.log"
    else
      note_manual "bash install_service.sh failed — fix and re-run"
    fi
  fi
fi

print_summary

if [[ "$SERVICE_DONE" -eq 1 ]]; then
  exit 0
fi

if [[ "$ASSUME_YES" -eq 1 ]]; then
  echo
  echo "Noninteractive setup finished (no foreground agent — service not started or already handled)."
  echo "Start later:  python agent.py   or   bash install_service.sh"
  exit 0
fi

echo
echo "--- Starting worker in this terminal (Ctrl+C to stop) ---"
echo "Tip: later run  bash install_service.sh  for a login background service."
exec python agent.py
