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

### Quick install

```bash
git clone https://github.com/kamposlargos/sway-session-restore.git
cd sway-session-restore
./install.sh
```

This will:
- Copy scripts to `~/.local/bin/`
- Install a sample `sway-session-appmap.json` (skipped if one already exists)
- Enable the systemd auto-save timer (every 5 minutes)
- Print the Sway config lines you need to add

### Add to Sway config

Add the following to `~/.config/sway/config`:

```bash
# Restore session on startup
exec ~/.local/bin/sway-session-restore.py

# Save session and exit
bindsym $mod+Shift+e exec ~/.local/bin/sway-session-save.py --force && swaymsg exit

# Manual save
bindsym $mod+Shift+s exec ~/.local/bin/sway-session-save.py --force && notify-send "Session saved"
```

### App mapping (optional)

**Most apps work out of the box.** The save script automatically detects launch commands from `/proc/PID/cmdline`. The app mapping file (`sway-session-appmap.json`) is only needed to override commands when auto-detection doesn't work correctly — most commonly for Chrome/Chromium PWAs.

To customize, edit `~/.local/bin/sway-session-appmap.json`:

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

- **Direct entries**: `"app_id": ["command", "args"]` — override for a specific app_id
- **`_patterns`**: regex patterns matched against `app_id` — useful for Chrome PWAs where `app_id` is dynamic (e.g., `chrome-github.com__-Profile_1`)

> **Tip:** Run `swaymsg -t get_tree | jq '.. | .app_id? // empty'` to discover app_ids for your running windows.

### Auto-save

The installer enables a systemd timer that saves every 5 minutes. Auto-save uses a safety check (no `--force`), so it won't overwrite a good session if most windows are closed.

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
