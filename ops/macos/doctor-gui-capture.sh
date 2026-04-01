#!/bin/bash

set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

TMP_IMAGE="${TMPDIR:-/tmp}/admiranext-gui-capture-check-$$.jpg"
GUI_DOMAIN="gui/$(id -u)"

cleanup() {
  rm -f "$TMP_IMAGE"
}
trap cleanup EXIT

print_section() {
  echo
  echo "[$1]"
}

print_section "Sesion"
echo "Usuario: $(id -un)"
echo "UID: $(id -u)"
if launchctl print "$GUI_DOMAIN" >/dev/null 2>&1; then
  echo "GUI session: OK"
else
  echo "GUI session: FAIL"
  echo "No hay sesion Aqua disponible."
fi

print_section "Accesibilidad"
if APPS_OUTPUT=$(osascript -e 'tell application "System Events" to get name of every process whose background only is false' 2>&1); then
  echo "System Events: OK"
  printf '%s\n' "$APPS_OUTPUT" | head -n 1
else
  echo "System Events: FAIL"
  printf '%s\n' "$APPS_OUTPUT"
  echo "Revisa Ajustes del Sistema > Privacidad y seguridad > Accesibilidad."
fi

print_section "Grabacion de pantalla"
if screencapture -x -t jpg "$TMP_IMAGE" >/dev/null 2>&1 && [[ -s "$TMP_IMAGE" ]]; then
  echo "screencapture: OK ($(wc -c < "$TMP_IMAGE" | tr -d ' ') bytes)"
else
  echo "screencapture: FAIL"
  echo "Revisa Ajustes del Sistema > Privacidad y seguridad > Grabacion de pantalla."
fi

print_section "API local"
if curl -sSf http://127.0.0.1:3030/api/teamwork/snapshots >/dev/null 2>&1; then
  echo "Control local: OK"
else
  echo "Control local: no responde en 127.0.0.1:3030"
fi
