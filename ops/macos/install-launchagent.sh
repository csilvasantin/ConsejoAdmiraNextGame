#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
LABEL="com.admiranext.control"
GUI_DOMAIN="gui/$(id -u)"
AGENT_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/AdmiraNext"
PLIST_PATH="$AGENT_DIR/$LABEL.plist"
SCRIPT_PATH="$REPO_ROOT/ops/macos/start-control-agent.sh"
STDOUT_PATH="$LOG_DIR/control-agent.out.log"
STDERR_PATH="$LOG_DIR/control-agent.err.log"

mkdir -p "$AGENT_DIR" "$LOG_DIR"
chmod +x "$SCRIPT_PATH"

if ! launchctl print "$GUI_DOMAIN" >/dev/null 2>&1; then
  echo "No hay sesion grafica Aqua disponible para $(id -un)." >&2
  echo "Inicia sesion en el Mac y ejecuta este instalador desde esa sesion." >&2
  exit 1
fi

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>$SCRIPT_PATH</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$REPO_ROOT</string>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>ThrottleInterval</key>
  <integer>5</integer>

  <key>LimitLoadToSessionType</key>
  <array>
    <string>Aqua</string>
  </array>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>ADMIRANEXT_CONTROL_ROOT</key>
    <string>$REPO_ROOT</string>
  </dict>

  <key>StandardOutPath</key>
  <string>$STDOUT_PATH</string>

  <key>StandardErrorPath</key>
  <string>$STDERR_PATH</string>
</dict>
</plist>
PLIST

plutil -lint "$PLIST_PATH" >/dev/null
launchctl bootout "$GUI_DOMAIN" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "$GUI_DOMAIN" "$PLIST_PATH"
launchctl enable "$GUI_DOMAIN/$LABEL"
launchctl kickstart -k "$GUI_DOMAIN/$LABEL"

sleep 2

echo "LaunchAgent instalado: $PLIST_PATH"
echo "Logs:"
echo "  $STDOUT_PATH"
echo "  $STDERR_PATH"
echo
launchctl print "$GUI_DOMAIN/$LABEL" | grep -E "state =|pid =|path =" || true
echo
echo "Valida ahora:"
echo "  http://127.0.0.1:3030/control.html"
