# sway-session-restore

Save and restore window layouts for [Sway](https://swaywm.org/) — including tiling structure, split directions, and window sizes.

Designed for dual-boot setups or any situation where Sway sessions don't persist across reboots.

## Features

- **Full layout restoration** — tiling structure, split directions (horizontal/vertical), and nested containers
- **Accurate size restoration** — top-down ratio-based resizing that preserves window proportions
- **Floating window support** — position and size
- **App mapping** — configurable `app_id` → launch command mapping with regex pattern support
- **Chrome/Chromium PWA support** — pattern-based matching for Progressive Web App windows
- **Auto-save** — systemd timer for periodic saves (default: every 5 minutes)
- **Safety check** — auto-save skips if window count drops below 50% (prevents saving during window management)
- **Focused workspace memory** — restores focus to the previously active workspace

## How It Works

### Save (`sway-session-save.py`)

1. Reads the Sway window tree via `swaymsg -t get_tree`
2. Extracts workspace layout, container structure, and window sizes
3. Resolves launch commands using the appmap or `/proc/PID/cmdline`
4. Flattens single-child containers (removes unnecessary nesting from Sway's tree)
5. Saves to `~/.local/state/sway-session/session.json`

### Restore (`sway-session-restore.py`)

Uses a **2-pass algorithm** to accurately reconstruct layouts:

**Pass 1 — Anchors:** For each workspace, launch the first leaf window of every top-level node. This establishes the top-level split structure.

**Pass 2 — Subtrees:** For each top-level node, recursively build the remaining container structure by launching windows at the correct focus positions.

**Size restoration** uses a **top-down approach**: resize siblings at the shallowest level first, then recurse into child containers. This prevents deeper resizes from being overwritten by shallower ones. Each container's last child is left unresized (it fills the remaining space automatically).

## Installation

### 1. Copy scripts

```bash
cp sway-session-save.py ~/.local/bin/
cp sway-session-restore.py ~/.local/bin/
cp sway-session-appmap.json ~/.local/bin/
chmod +x ~/.local/bin/sway-session-save.py
chmod +x ~/.local/bin/sway-session-restore.py
```

### 2. Configure app mapping

Edit `~/.local/bin/sway-session-appmap.json` to match your apps:

```json
{
    "_patterns": {
        "^chrome-.*__": ["google-chrome-stable", "about:blank"]
    },
    "com.mitchellh.ghostty": ["ghostty"],
    "foot": ["foot"],
    "firefox": ["firefox"]
}
```

- **Direct entries**: `"app_id": ["command", "args"]`
- **`_patterns`**: regex patterns matched against `app_id` — useful for Chrome PWAs where `app_id` is dynamic (e.g., `chrome-github.com__-Profile_1`)

> **Tip:** Run `swaymsg -t get_tree | jq '.. | .app_id? // empty'` to discover app_ids for your running windows.

### 3. Add to Sway config

```bash
# Restore session on startup
exec ~/.local/bin/sway-session-restore.py

# Save session and exit (e.g., bind to $mod+Shift+e)
bindsym $mod+Shift+e exec ~/.local/bin/sway-session-save.py --force && swaymsg exit

# Manual save (e.g., bind to $mod+Shift+s)
bindsym $mod+Shift+s exec ~/.local/bin/sway-session-save.py --force && notify-send "Session saved"
```

### 4. Auto-save timer (optional)

```bash
cp sway-session-save.service ~/.config/systemd/user/
cp sway-session-save.timer ~/.config/systemd/user/
systemctl --user enable --now sway-session-save.timer
```

The timer saves every 5 minutes. Auto-save uses the safety check (no `--force`), so it won't overwrite a good session if most windows are closed.

## File Locations

| File | Path |
|------|------|
| Session data | `~/.local/state/sway-session/session.json` |
| Backup | `~/.local/state/sway-session/session.json.bak` |
| Restore log | `~/.local/state/sway-session/restore.log` |
| App mapping | `~/.local/bin/sway-session-appmap.json` |

## Chrome / Chromium PWA Tips

Chrome PWAs have dynamic `app_id` values like `chrome-docs.google.com__-Profile_1`. Use regex patterns in `_patterns` to match them:

```json
{
    "_patterns": {
        "^chrome-docs\\.google\\.com__": [
            "google-chrome-stable",
            "--app=https://docs.google.com",
            "--class=GoogleDocs",
            "--profile-directory=Default"
        ],
        "^chrome-.*__": ["google-chrome-stable", "about:blank"]
    }
}
```

Patterns are evaluated in order. Place specific patterns before catch-all patterns.

## Limitations

- **Tab restoration**: Individual browser tabs are not saved. Use `--restore-last-session` or configure your browser's startup behavior.
- **Window matching**: Restore identifies windows by `app_id`. If multiple instances of the same app launch, they may not be placed in the exact original order.
- **Startup timing**: Some apps take longer to create their window. The default timeout is 15 seconds per window.
- **Multi-output**: Workspaces are restored to outputs by name. If output names change, workspaces may appear on different monitors.

## Requirements

- Python 3.10+
- Sway
- `swaymsg`

## License

MIT
