#!/bin/bash
# Installs the Mac reporter: copies it outside CloudStorage and loads an hourly
# LaunchAgent. Safe to rerun. Run this only after the ops-board repo exists.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/Library/Application Support/OpsBoard"
PY="/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"
LABEL="com.justinevans.ops-board-report"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$DEST"
cp "$SRC/local_report.py" "$DEST/local_report.py"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DEST/local_report.py</string></array>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$DEST/report.log</string>
  <key>StandardErrorPath</key><string>$DEST/report.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Loaded $LABEL"
