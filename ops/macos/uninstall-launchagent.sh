#!/bin/bash

set -euo pipefail

LABEL="com.admiranext.control"
GUI_DOMAIN="gui/$(id -u)"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "$GUI_DOMAIN" "$PLIST_PATH" >/dev/null 2>&1 || true
rm -f "$PLIST_PATH"

echo "LaunchAgent retirado: $PLIST_PATH"
echo "Los logs se mantienen en ~/Library/Logs/AdmiraNext/."
