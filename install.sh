#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/.local/bin"
SYSTEMD_DIR="$HOME/.config/systemd/user"

echo "=== sway-session-restore installer ==="
echo ""

# 1. Install scripts
mkdir -p "$BIN_DIR"

cp "$SCRIPT_DIR/sway-session-save.py" "$BIN_DIR/"
cp "$SCRIPT_DIR/sway-session-restore.py" "$BIN_DIR/"
chmod +x "$BIN_DIR/sway-session-save.py"
chmod +x "$BIN_DIR/sway-session-restore.py"
echo "[ok] Installed scripts to $BIN_DIR/"

# 2. Install appmap (skip if already exists)
if [ -f "$BIN_DIR/sway-session-appmap.json" ]; then
    echo "[skip] $BIN_DIR/sway-session-appmap.json already exists"
else
    cp "$SCRIPT_DIR/sway-session-appmap.json" "$BIN_DIR/"
    echo "[ok] Installed sample appmap to $BIN_DIR/"
fi

# 3. Install systemd units
mkdir -p "$SYSTEMD_DIR"

cp "$SCRIPT_DIR/sway-session-save.service" "$SYSTEMD_DIR/"
cp "$SCRIPT_DIR/sway-session-save.timer" "$SYSTEMD_DIR/"
systemctl --user daemon-reload
systemctl --user enable --now sway-session-save.timer
echo "[ok] Enabled auto-save timer (every 5 minutes)"

# 4. Show sway config instructions
echo ""
echo "=== Add the following to your Sway config (~/.config/sway/config): ==="
echo ""
echo '# Restore session on startup'
echo 'exec ~/.local/bin/sway-session-restore.py'
echo ''
echo '# Save session and exit'
echo 'bindsym $mod+Shift+e exec ~/.local/bin/sway-session-save.py --force && swaymsg exit'
echo ''
echo '# Manual save'
echo 'bindsym $mod+Shift+s exec ~/.local/bin/sway-session-save.py --force && notify-send "Session saved"'
echo ""
echo "=== Installation complete ==="
echo ""
echo "Edit $BIN_DIR/sway-session-appmap.json to configure your apps."
echo "Tip: run 'swaymsg -t get_tree | jq \".. | .app_id? // empty\"' to discover app_ids."
