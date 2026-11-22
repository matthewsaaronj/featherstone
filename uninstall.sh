#!/bin/bash
# Featherstone — stop and remove the app's background jobs and its Tailscale
# address. Your data (~/featherstone-data) and the app folder are left in place;
# delete them yourself if you want them gone.
#
#   bash ~/featherstone/uninstall.sh [--name NAME] [--https-port N]
set -uo pipefail
NAME="featherstone"; HTTPS_PORT=8444
while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --https-port) HTTPS_PORT="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
done
LABEL="com.featherstone"; [ "$NAME" != "featherstone" ] && LABEL="com.featherstone.$NAME"
for l in "$LABEL" "$LABEL.tick"; do
  f="$HOME/Library/LaunchAgents/$l.plist"
  if [ -f "$f" ]; then launchctl unload "$f" 2>/dev/null; rm -f "$f"; echo "  ✓ removed $l"; fi
done
TS="$(command -v tailscale 2>/dev/null || true)"
[ -z "$TS" ] && [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ] && TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
if [ -n "$TS" ]; then "$TS" serve --https="$HTTPS_PORT" off 2>/dev/null && echo "  ✓ removed Tailscale address on port $HTTPS_PORT"; fi
echo "Done. Your data is still in ~/featherstone-data (or ~/featherstone-data-$NAME)."
