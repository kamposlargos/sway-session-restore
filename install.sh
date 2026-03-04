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

# 4. Add exec line to sway config
SWAY_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/sway/config"
EXEC_LINE="exec ~/.local/bin/sway-session-restore.py"

if [ -f "$SWAY_CONFIG" ]; then
    if grep -qF "sway-session-restore.py" "$SWAY_CONFIG"; then
        echo "[skip] Sway config already contains restore exec line"
    else
        echo "" >> "$SWAY_CONFIG"
        echo "# Restore session on startup (sway-session-restore)" >> "$SWAY_CONFIG"
        echo "$EXEC_LINE" >> "$SWAY_CONFIG"
        echo "[ok] Added restore exec line to $SWAY_CONFIG"
    fi
else
    echo "[warn] Sway config not found at $SWAY_CONFIG"
    echo "  Add this line manually: $EXEC_LINE"
fi

echo ""
echo "=== Installation complete ==="
echo ""
echo "Optional keybindings for your Sway config:"
echo ""
echo '# Save session and exit'
echo 'bindsym $mod+Shift+e exec ~/.local/bin/sway-session-save.py --force && swaymsg exit'
echo ''
echo '# Manual save'
echo 'bindsym $mod+Shift+s exec ~/.local/bin/sway-session-save.py --force && notify-send "Session saved"'
