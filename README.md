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
- Add `exec ~/.local/bin/sway-session-restore.py` to your Sway config (skipped if already present)

### Optional keybindings

Add these to `~/.config/sway/config` if you want manual save and save-on-exit (adjust keys to your preference):

```bash
# Save session and exit Sway
bindsym $mod+Shift+e exec ~/.local/bin/sway-session-save.py --force && swaymsg exit

# Manual save
bindsym $mod+Shift+s exec ~/.local/bin/sway-session-save.py --force && notify-send "Session saved"
```

## Usage

### Saving

- **Auto-save**: The systemd timer saves every 5 minutes automatically. If the window count drops below 50% of the previous save, the save is skipped to prevent overwriting a good session.
- **Manual save**: Press your save keybinding (e.g., `$mod+Shift+s`) or run `~/.local/bin/sway-session-save.py --force` directly. The `--force` flag bypasses the safety check.
- **Save on exit**: Bind a key (e.g., `$mod+Shift+e`) to save and exit Sway in one step.

### Restoring

Session is restored automatically on Sway startup via `exec` in your config. You can also run `~/.local/bin/sway-session-restore.py` manually at any time.

### App mapping (optional)

**Most apps work out of the box** without any configuration:

- **Standard apps** (terminals, file managers, browsers, etc.) — detected automatically via `/proc/PID/cmdline`
- **Chrome/Chromium PWAs** — detected automatically from `app_id` pattern (`chrome-DOMAIN__-Profile_N` → `--app=https://DOMAIN --profile-directory=Profile N`)
- **Electron apps** (VS Code, Obsidian, Discord, etc.) — resolved from a built-in mapping of known app_ids

The app mapping file (`sway-session-appmap.json`) is only needed to **override** auto-detection for edge cases. To customize, edit `~/.local/bin/sway-session-appmap.json`:

```json
{
    "_patterns": {
        "^MyApp$": ["my-app", "--some-flag"]
    },
    "some.custom.app": ["custom-command"]
}
```

- **Direct entries**: `"app_id": ["command", "args"]` — override for a specific app_id
- **`_patterns`**: regex patterns matched against `app_id`

> **Tip:** Run `swaymsg -t get_tree | jq '.. | .app_id? // empty'` to discover app_ids for your running windows.


## File Locations

| File | Path |
|------|------|
| Session data | `~/.local/state/sway-session/session.json` |
| Backup | `~/.local/state/sway-session/session.json.bak` |
| Restore log | `~/.local/state/sway-session/restore.log` |
| App mapping | `~/.local/bin/sway-session-appmap.json` |

## Chrome / Chromium PWA Tips

Chrome PWAs are **automatically detected** from their `app_id` (format: `chrome-DOMAIN__-Profile_N`). The save script generates the correct `--app=https://DOMAIN --profile-directory=Profile N` command automatically.

If you need to customize a specific PWA (e.g., to add `--class`), use an appmap override:

```json
{
    "_patterns": {
        "^chrome-docs\\.google\\.com__": [
            "google-chrome-stable",
            "--app=https://docs.google.com",
            "--class=GoogleDocs",
            "--profile-directory=Default"
        ]
    }
}
```

Appmap overrides take priority over auto-detection. Patterns are evaluated in order.

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
