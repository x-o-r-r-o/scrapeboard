#!/usr/bin/env bash
# Install Scrapeboard worker as a background service that starts on boot/login
# and keeps running after the terminal closes.
#
# Usage (from the worker directory, after wizard / worker_config.json exists):
#   bash install_service.sh
#   bash install_service.sh --uninstall
#
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
OS="$(uname -s)"
NAME="scrapeboard-worker"
PYTHON=""
UNINSTALL=0

for arg in "$@"; do
  case "$arg" in
    --uninstall|-u) UNINSTALL=1 ;;
    -h|--help)
      echo "Usage: bash install_service.sh [--uninstall]"
      exit 0
      ;;
  esac
done

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "ERROR: Python not found. Run setup_and_run.sh first."
  exit 1
fi

if [[ ! -f "$ROOT/worker_config.json" && "$UNINSTALL" -eq 0 ]]; then
  echo "ERROR: worker_config.json missing."
  echo "Run first:  $PYTHON agent.py --setup"
  exit 1
fi

mkdir -p "$ROOT/logs" "$ROOT/work"

install_macos() {
  local plist="$HOME/Library/LaunchAgents/com.scrapeboard.worker.plist"
  if [[ "$UNINSTALL" -eq 1 ]]; then
    launchctl bootout "gui/$(id -u)/com.scrapeboard.worker" 2>/dev/null || true
    launchctl unload "$plist" 2>/dev/null || true
    rm -f "$plist"
    echo "Uninstalled LaunchAgent: $plist"
    return
  fi
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.scrapeboard.worker</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON}</string>
    <string>${ROOT}/agent.py</string>
    <string>--service</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${ROOT}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>${ROOT}/logs/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${ROOT}/logs/launchd.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
  </dict>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/com.scrapeboard.worker" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null || launchctl load -w "$plist"
  launchctl kickstart -k "gui/$(id -u)/com.scrapeboard.worker" 2>/dev/null || true
  echo "Installed macOS LaunchAgent (starts at login, KeepAlive)."
  echo "  plist: $plist"
  echo "  logs:  $ROOT/logs/worker.log"
  echo "  status: launchctl print gui/$(id -u)/com.scrapeboard.worker | head"
}

install_linux() {
  local unit_dir="$HOME/.config/systemd/user"
  local unit="$unit_dir/${NAME}.service"
  if [[ "$UNINSTALL" -eq 1 ]]; then
    systemctl --user disable --now "$NAME" 2>/dev/null || true
    rm -f "$unit"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "Uninstalled systemd user unit: $unit"
    return
  fi
  mkdir -p "$unit_dir"
  cat > "$unit" <<EOF
[Unit]
Description=Scrapeboard scrape worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${ROOT}
ExecStart=${PYTHON} ${ROOT}/agent.py --service
Restart=always
RestartSec=10
KillMode=mixed
TimeoutStopSec=60
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now "$NAME"
  # Survive logout (best-effort; may need sudo once)
  if command -v loginctl >/dev/null 2>&1; then
    loginctl enable-linger "$(id -un)" 2>/dev/null || \
      echo "NOTE: run once for boot without login:  sudo loginctl enable-linger $(id -un)"
  fi
  echo "Installed systemd user service (starts at login/boot with linger)."
  echo "  unit:   $unit"
  echo "  logs:   $ROOT/logs/worker.log  (also: journalctl --user -u $NAME -f)"
  echo "  status: systemctl --user status $NAME"
}

case "$OS" in
  Darwin) install_macos ;;
  Linux) install_linux ;;
  *)
    echo "Unsupported OS: $OS"
    echo "On Windows use:  install_service.bat"
    exit 1
    ;;
esac
