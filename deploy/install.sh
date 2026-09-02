#!/usr/bin/env bash
# Install the console as a launchd user agent: starts at login, restarts on
# crash, logs to ./logs. Removes cleanly with deploy/uninstall.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.voicebot.console"
AGENTS="$HOME/Library/LaunchAgents"
PLIST="$AGENTS/$LABEL.plist"

[ -x "$ROOT/.venv/bin/python" ] || { echo "No .venv — run 'make setup' first." >&2; exit 1; }

mkdir -p "$AGENTS" "$ROOT/logs"
sed -e "s|__ROOT__|$ROOT|g" -e "s|__HOME__|$HOME|g" \
    "$ROOT/deploy/$LABEL.plist" > "$PLIST"

# bootout is the modern replacement for unload; ignore the error when absent.
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true

# bootout returns before the process has finished exiting, and bootstrapping
# over a still-draining service fails with a bare "Input/output error".
for _ in $(seq 1 30); do
  launchctl list | grep -q "$LABEL" || break
  sleep 1
done

if ! launchctl bootstrap "gui/$UID" "$PLIST"; then
  echo "bootstrap failed — is an old instance still running?" >&2
  echo "  launchctl list | grep $LABEL" >&2
  exit 1
fi
launchctl enable "gui/$UID/$LABEL"

echo "Installed $LABEL"
echo "  console : http://127.0.0.1:8788"
echo "  logs    : $ROOT/logs/console.log"
echo "  stop    : launchctl bootout gui/$UID/$LABEL"
echo
echo "First start loads models and warms the synthesiser — allow ~60s."
