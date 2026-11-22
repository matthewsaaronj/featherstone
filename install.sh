#!/bin/bash
# Featherstone — installer for a Mac.
#
# One command sets everything up (see INSTALL.md):
#   curl -fsSL https://raw.githubusercontent.com/matthewsaaronj/featherstone/main/install.sh | bash
#
# What it does:
#   1. downloads the app into ~/featherstone (or uses the folder it's run from)
#   2. installs what the app needs into ~/featherstone-venv
#   3. starts the app now and every time the Mac starts (launchd), plus a
#      15-minute job for daily upkeep, backups and phone notifications
#   4. publishes the app privately to your phone with Tailscale
# Your data lives in ~/featherstone-data. Running this again updates the app and
# keeps your data.
#
# Options (rarely needed):
#   --name NAME        a second, separate copy on the same Mac (own data, own address)
#   --port N           local port (default 5057)
#   --https-port N     Tailscale port your phone uses (default 8444)
#   --dry-run          show what would happen, change nothing
set -euo pipefail

REPO_URL="https://github.com/matthewsaaronj/featherstone"
NAME="featherstone"
PORT=5057
HTTPS_PORT=8444
DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --https-port) HTTPS_PORT="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
done
case "$NAME" in *[!a-z0-9-]*|"") echo "--name may only use lowercase letters, numbers and dashes."; exit 2 ;; esac

bold() { printf "\n\033[1m%s\033[0m\n" "$1"; }
ok()   { printf "  ✓ %s\n" "$1"; }
warn() { printf "  ! %s\n" "$1"; }
run()  { if [ "$DRY" = 1 ]; then echo "  [dry run] $*"; else "$@"; fi; }

SUFFIX=""; [ "$NAME" != "featherstone" ] && SUFFIX="-$NAME"
DATA_DIR="$HOME/featherstone-data$SUFFIX"
VENV="$HOME/featherstone-venv"
LABEL="com.featherstone"; if [ -n "$SUFFIX" ]; then LABEL="com.featherstone.$NAME"; fi
AGENTS="$HOME/Library/LaunchAgents"

# ---------- 1. The app's files ----------
bold "1/4  Getting the app"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$HERE" ] && [ -f "$HERE/wsgi.py" ] && [ -d "$HERE/webapp" ]; then
  APP_DIR="$HERE"
  ok "Using the app in $APP_DIR"
else
  APP_DIR="$HOME/featherstone"
  TMP="$(mktemp -d)"
  run curl -fsSL "$REPO_URL/archive/refs/heads/main.tar.gz" -o "$TMP/app.tar.gz"
  run mkdir -p "$APP_DIR"
  # Replace the code but never the data (which lives in ~/featherstone-data).
  run tar -xzf "$TMP/app.tar.gz" -C "$APP_DIR" --strip-components 1
  ok "Downloaded to $APP_DIR"
fi
case "$APP_DIR" in
  "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*)
    warn "The app is inside Documents, Desktop or Downloads. macOS can block background"
    warn "jobs from reading those folders. If the app doesn't start, move the folder"
    warn "into your home folder and run this again." ;;
esac

# ---------- 2. Python and the app's libraries ----------
bold "2/4  Installing what the app needs"
PY=""
for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  p="$(command -v "$c" 2>/dev/null || true)"
  if [ -n "$p" ] && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then PY="$p"; break; fi
done
if [ -z "$PY" ]; then
  warn "This Mac needs Python 3.10 or newer. Opening the download page now:"
  warn "install the 'macOS 64-bit universal2 installer', then run this command again."
  [ "$DRY" = 1 ] || open "https://www.python.org/downloads/macos/"
  exit 1
fi
ok "Python: $("$PY" --version)"
if [ ! -x "$VENV/bin/python" ]; then run "$PY" -m venv "$VENV"; fi
run "$VENV/bin/pip" install -q --upgrade pip
run "$VENV/bin/pip" install -q -r "$APP_DIR/requirements.txt"
ok "Libraries installed in $VENV"
run mkdir -p "$DATA_DIR/logs"

# ---------- 3. Background jobs ----------
bold "3/4  Starting the app"
plist() {  # $1 label, $2 program+args (as <string> lines), $3 extra keys, $4 log name
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$1</string>
    <key>ProgramArguments</key>
    <array>
$2
    </array>
    <key>WorkingDirectory</key><string>$APP_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>FEATHERSTONE_DATA_DIR</key><string>$DATA_DIR</string>
        <key>DATABASE_URI</key><string>sqlite:///$DATA_DIR/scripture.db</string>
    </dict>
$3
    <key>StandardOutPath</key><string>$DATA_DIR/logs/$4.log</string>
    <key>StandardErrorPath</key><string>$DATA_DIR/logs/$4-error.log</string>
</dict>
</plist>
EOF
}
APP_PLIST="$(plist "$LABEL" "        <string>$VENV/bin/gunicorn</string>
        <string>--bind</string><string>127.0.0.1:$PORT</string>
        <string>--workers</string><string>1</string>
        <string>--timeout</string><string>60</string>
        <string>--access-logfile</string><string>-</string>
        <string>wsgi:app</string>" "    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>" "app")"
TICK_PLIST="$(plist "$LABEL.tick" "        <string>$VENV/bin/flask</string>
        <string>--app</string><string>wsgi:app</string>
        <string>tick</string>" "    <key>StartInterval</key><integer>900</integer>
    <key>RunAtLoad</key><true/>" "tick")"

install_job() {  # $1 label, $2 plist text
  local f="$AGENTS/$1.plist"
  if [ "$DRY" = 1 ]; then echo "  [dry run] would write $f:"; echo "$2" | sed 's/^/      /'; return; fi
  mkdir -p "$AGENTS"
  launchctl unload "$f" 2>/dev/null || true
  printf '%s\n' "$2" > "$f"
  plutil -lint "$f" >/dev/null
  launchctl load "$f"
}
install_job "$LABEL" "$APP_PLIST"
install_job "$LABEL.tick" "$TICK_PLIST"
if [ "$DRY" = 0 ]; then
  for _ in $(seq 1 30); do curl -fs -o /dev/null "http://127.0.0.1:$PORT/" && break; sleep 1; done
  if curl -fs -o /dev/null "http://127.0.0.1:$PORT/"; then ok "Running on this Mac at http://localhost:$PORT"
  else warn "The app didn't start. Details are in $DATA_DIR/logs/app-error.log"; exit 1; fi
fi

# ---------- 4. Tailscale: a private address for your phone ----------
bold "4/4  Connecting your phone (Tailscale)"
TS="$(command -v tailscale 2>/dev/null || true)"
[ -z "$TS" ] && [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ] && TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
if [ -z "$TS" ]; then
  warn "Tailscale isn't installed on this Mac. Install it from https://tailscale.com/download/mac,"
  warn "sign in, then run this command again to finish."
  exit 0
fi
if ! "$TS" status >/dev/null 2>&1; then
  warn "Tailscale is installed but not signed in. Open Tailscale from the menu bar, sign in,"
  warn "then run this command again to finish."
  exit 0
fi
run "$TS" serve --bg --https="$HTTPS_PORT" "http://localhost:$PORT"
HOST="$("$TS" status --json 2>/dev/null | "$VENV/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
ADDRESS="https://$HOST:$HTTPS_PORT"
[ "$DRY" = 1 ] || echo "$ADDRESS" > "$DATA_DIR/address.txt"

bold "All set!"
cat <<EOF

  Your app's address:   $ADDRESS

  On your iPhone:
   1. Install the Tailscale app and sign in with the same account as this Mac.
   2. In Safari, open the address above.
   3. Tap Share → Add to Home Screen, then open "Featherstone" from your Home Screen.
   4. In the app: Settings → Turn on notifications → Allow, then Send a test.

  Keep this Mac on and awake (System Settings → Energy) so the app is
  always reachable. Your data is in $DATA_DIR.
EOF
