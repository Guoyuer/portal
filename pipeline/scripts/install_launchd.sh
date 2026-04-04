#!/bin/bash
# Install macOS launchd job for automatic data sync to Cloudflare R2.
# Runs sync.py daily at 9AM and on user login.
#
# Prerequisites:
#   npm install -g wrangler && wrangler login
#   pip install -r pipeline/requirements.txt (or use a venv)
#
# Usage:
#   bash pipeline/scripts/install_launchd.sh         # install
#   bash pipeline/scripts/install_launchd.sh remove   # uninstall

set -euo pipefail

LABEL="com.portal.sync"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SYNC_SCRIPT="$SCRIPT_DIR/sync.py"
LOG_DIR="$HOME/.local/log"

# Find Python (prefer venv if available)
VENV_PYTHON="$SCRIPT_DIR/../.venv/bin/python"
if [ -f "$VENV_PYTHON" ]; then
    PYTHON="$VENV_PYTHON"
else
    PYTHON="$(which python3)"
fi

if [ "${1:-}" = "remove" ]; then
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "Removed $LABEL"
    exit 0
fi

# Verify prerequisites
if ! command -v wrangler &>/dev/null; then
    echo "Error: wrangler not found. Install with: npm install -g wrangler && wrangler login"
    exit 1
fi

mkdir -p "$PLIST_DIR" "$LOG_DIR"

cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SYNC_SCRIPT</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/portal-sync.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/portal-sync.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
EOF

launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo "Installed $LABEL"
echo "  Python: $PYTHON"
echo "  Script: $SYNC_SCRIPT"
echo "  Log:    $LOG_DIR/portal-sync.log"
echo "  Schedule: daily 9AM + on login"
