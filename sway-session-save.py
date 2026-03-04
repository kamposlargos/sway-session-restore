#!/usr/bin/env python3
"""Sway session save script.
Parses swaymsg -t get_tree output and saves workspace/window layout to JSON.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SAVE_DIR = Path.home() / ".local" / "state" / "sway-session"
SAVE_FILE = SAVE_DIR / "session.json"
APPMAP_FILE = Path(__file__).parent / "sway-session-appmap.json"


def get_tree() -> dict:
    """Get JSON output from swaymsg -t get_tree."""
    result = subprocess.run(
        ["swaymsg", "-t", "get_tree"],
        capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def load_appmap() -> tuple[dict, list[tuple[str, list[str]]]]:
    """Load app_id → command override map. Returns (direct_map, pattern_list)."""
    direct = {}
    patterns = []
    if APPMAP_FILE.exists():
        with open(APPMAP_FILE) as f:
            data = json.load(f)
        # Pattern definitions
        pat_data = data.get("_patterns", {})
        for pat_str, cmd in pat_data.items():
            patterns.append((pat_str, cmd))
        # Direct map (exclude keys starting with _)
        direct = {k: v for k, v in data.items() if not k.startswith("_")}
    return direct, patterns


def get_command_from_pid(pid: int) -> list[str] | None:
    """Get command from /proc/PID/cmdline."""
    try:
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        if cmdline_path.exists():
            raw = cmdline_path.read_bytes()
            if raw:
                parts = raw.split(b"\x00")
                cmd = [p.decode("utf-8", errors="replace") for p in parts if p]
                if cmd:
                    return cmd
    except (PermissionError, OSError):
        pass
    return None


def get_identifier(node: dict) -> tuple[str | None, str]:
    """Get app identifier from node. Returns (identifier, source)."""
    app_id = node.get("app_id")
    if app_id:
        return app_id, "app_id"
    wp = node.get("window_properties") or {}
    wclass = wp.get("class")
    if wclass:
        return wclass, "class"
    return None, ""


def resolve_chrome_pwa(identifier: str) -> list[str] | None:
    """Auto-resolve Chrome/Chromium PWA commands from app_id.
    Chrome PWA app_id format: chrome-DOMAIN__-Profile_N
    Example: chrome-claude.ai__-Profile_20 -> google-chrome-stable --app=https://claude.ai --profile-directory=Profile 20"""
    m = re.match(r"^chrome-(.+)__-(.+)$", identifier)
    if not m:
        return None
    domain = m.group(1)
    profile = m.group(2).replace("_", " ")
    # Detect browser command
    browser = "google-chrome-stable"
    for candidate in ["google-chrome-stable", "google-chrome", "chromium", "chromium-browser"]:
        if shutil.which(candidate):
            browser = candidate
            break
    return [browser, f"--app=https://{domain}", f"--profile-directory={profile}"]


def resolve_electron(identifier: str, pid: int | None) -> list[str] | None:
    """Auto-resolve Electron app commands.
    Electron apps have unhelpful /proc/PID/cmdline (e.g., /usr/lib/electron/electron ...).
    Map known app_ids to their launcher commands."""
    # Well-known Electron app_id -> command mappings
    electron_apps = {
        "code-oss": "code",
        "code": "code",
        "code-insiders": "code-insiders",
        "obsidian": "obsidian",
        "discord": "discord",
        "slack": "slack",
        "signal": "signal-desktop",
        "spotify": "spotify",
    }
    cmd = electron_apps.get(identifier)
    if cmd and shutil.which(cmd):
        return [cmd]
    # Heuristic: if /proc/PID/cmdline contains /electron, treat as Electron
    if pid:
        proc_cmd = get_command_from_pid(pid)
        if proc_cmd and any("electron" in arg for arg in proc_cmd[:2]):
            # Try identifier as command
            if shutil.which(identifier):
                return [identifier]
    return None


def resolve_command(
    identifier: str,
    pid: int | None,
    appmap: dict,
    patterns: list[tuple[str, list[str]]],
) -> list[str]:
    """Resolve the launch command for an app."""
    # 1. Appmap direct map (user override, highest priority)
    if identifier in appmap:
        return appmap[identifier]

    # 2. Appmap pattern matching (user override)
    for pat_str, cmd in patterns:
        if re.search(pat_str, identifier):
            return cmd

    # 3. Chrome/Chromium PWA auto-detection
    chrome_cmd = resolve_chrome_pwa(identifier)
    if chrome_cmd:
        return chrome_cmd

    # 4. Electron app auto-detection
    electron_cmd = resolve_electron(identifier, pid)
    if electron_cmd:
        return electron_cmd

    # 5. Get from /proc/PID/cmdline
    if pid:
        cmd = get_command_from_pid(pid)
        if cmd:
            return cmd

    # 6. Fallback: use identifier as command
    return [identifier]


def process_node(
    node: dict, appmap: dict, patterns: list[tuple[str, list[str]]]
) -> dict | None:
    """Recursively process a node to build session info."""
    identifier, id_source = get_identifier(node)
    children = node.get("nodes", [])
    rect = node.get("rect", {})
    window_rect = node.get("window_rect", {})

    # Window node (has app_id or class)
    if identifier:
        pid = node.get("pid")
        command = resolve_command(identifier, pid, appmap, patterns)
        is_floating = node.get("type") == "floating_con"

        result = {
            "type": "window",
            "app_id": identifier,
            "id_source": id_source,
            "command": command,
            "title": (node.get("name") or ""),
            "width": window_rect.get("width", rect.get("width", 0)),
            "height": window_rect.get("height", rect.get("height", 0)),
            "floating": is_floating,
            "focused": node.get("focused", False),
        }

        if is_floating:
            result["x"] = rect.get("x", 0)
            result["y"] = rect.get("y", 0)

        return result

    # Container node (has children but no app_id/class)
    if children:
        child_nodes = []
        for child in children:
            processed = process_node(child, appmap, patterns)
            if processed:
                child_nodes.append(processed)

        if child_nodes:
            # Flatten single-child containers (remove unnecessary nesting)
            if len(child_nodes) == 1:
                return child_nodes[0]
            return {
                "type": "container",
                "layout": node.get("layout", "splith"),
                "nodes": child_nodes,
            }

    return None


def process_workspace(
    ws: dict, appmap: dict, patterns: list[tuple[str, list[str]]]
) -> dict | None:
    """Process a workspace."""
    name = ws.get("name", "")

    # Exclude scratchpad
    if name == "__i3_scratch":
        return None

    # Tiling nodes
    nodes = []
    for child in ws.get("nodes", []):
        processed = process_node(child, appmap, patterns)
        if processed:
            nodes.append(processed)

    # Floating nodes
    floating_nodes = []
    for fnode in ws.get("floating_nodes", []):
        processed = process_node(fnode, appmap, patterns)
        if processed:
            processed["floating"] = True
            floating_nodes.append(processed)

    # Skip empty workspaces
    if not nodes and not floating_nodes:
        return None

    return {
        "name": name,
        "output": ws.get("output", ""),
        "layout": ws.get("layout", "splith"),
        "focused": ws.get("focused", False),
        "nodes": nodes,
        "floating_nodes": floating_nodes,
    }


def find_focused_workspace(tree: dict) -> str | None:
    """Find the currently focused workspace."""
    for output in tree.get("nodes", []):
        for ws in output.get("nodes", []):
            if ws.get("type") != "workspace":
                continue
            if has_focused_node(ws):
                return ws.get("name")
    return None


def has_focused_node(node: dict) -> bool:
    """Check if any descendant node is focused."""
    if node.get("focused"):
        return True
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        if has_focused_node(child):
            return True
    return False


def load_previous_window_count() -> int:
    """Get window count from existing session file."""
    if not SAVE_FILE.exists():
        return 0
    try:
        with open(SAVE_FILE) as f:
            data = json.load(f)
        return sum(count_windows(ws) for ws in data.get("workspaces", []))
    except (json.JSONDecodeError, KeyError):
        return 0


def save_session(force: bool = False):
    """Main: save session.
    force=True skips safety check (for manual save / exit save)."""
    tree = get_tree()
    appmap, patterns = load_appmap()

    workspaces = []

    # root -> outputs -> workspaces
    for output in tree.get("nodes", []):
        for ws in output.get("nodes", []):
            if ws.get("type") != "workspace":
                continue
            processed = process_workspace(ws, appmap, patterns)
            if processed:
                workspaces.append(processed)

    focused_workspace = find_focused_workspace(tree)

    total_windows = sum(count_windows(ws) for ws in workspaces)

    # Safety check: skip if window count dropped below 50% of previous
    if not force:
        prev_count = load_previous_window_count()
        if prev_count > 0 and total_windows < prev_count * 0.5:
            print(f"Safety skip: window count dropped significantly ({prev_count} -> {total_windows})")
            print("Use --force to override")
            return

    session = {
        "version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "focused_workspace": focused_workspace,
        "workspaces": workspaces,
    }

    SAVE_DIR.mkdir(parents=True, exist_ok=True)

    # Backup
    if SAVE_FILE.exists():
        shutil.copy2(SAVE_FILE, SAVE_FILE.with_suffix(".json.bak"))

    with open(SAVE_FILE, "w") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)

    print(f"Session saved: {SAVE_FILE}")
    print(f"Workspaces: {len(workspaces)}")
    print(f"Windows: {total_windows}")


def count_windows(ws_or_node: dict) -> int:
    """Count windows recursively."""
    count = 0
    for node in ws_or_node.get("nodes", []) + ws_or_node.get("floating_nodes", []):
        if node.get("type") == "window":
            count += 1
        elif node.get("type") == "container":
            count += count_windows(node)
    return count


if __name__ == "__main__":
    force = "--force" in sys.argv
    try:
        save_session(force=force)
    except subprocess.CalledProcessError as e:
        print(f"Error: swaymsg failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
