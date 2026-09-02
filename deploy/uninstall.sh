#!/usr/bin/env bash
set -euo pipefail
LABEL="com.voicebot.console"
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Removed $LABEL"
