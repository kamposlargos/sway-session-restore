#!/usr/bin/env python3
"""Sway session restore script.
Reads saved session.json and reconstructs window layout.

Restore algorithm (2-pass approach):
Focus workspace -> set split direction -> launch apps (auto-placed at focus)
-> recursively build container structure -> top-down size restoration
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SAVE_DIR = Path.home() / ".local" / "state" / "sway-session"
SAVE_FILE = SAVE_DIR / "session.json"
BACKUP_FILE = SAVE_DIR / "session.json.bak"
LOG_FILE = SAVE_DIR / "restore.log"

WINDOW_TIMEOUT = 15  # seconds to wait for window to appear

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def swaymsg(*args: str) -> str:
    """Execute a swaymsg command."""
    cmd = ["swaymsg"] + list(args)
    log.debug(f"swaymsg: {cmd}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.debug(f"swaymsg stderr: {result.stderr.strip()}")
    return result.stdout


def swaymsg_json(*args: str) -> list | dict:
    """Execute a swaymsg command and return JSON."""
    result = subprocess.run(
        ["swaymsg"] + list(args),
        capture_output=True, text=True
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def command_exists(cmd: str) -> bool:
    """Check if a command exists on the system."""
    return shutil.which(cmd) is not None


def load_session() -> dict | None:
    """Load session file."""
    for path in [SAVE_FILE, BACKUP_FILE]:
        if path.exists():
            try:
                with open(path) as f:
                    data = json.load(f)
                if data.get("version") == 1 and data.get("workspaces"):
                    log.info(f"Session loaded: {path}")
                    return data
            except (json.JSONDecodeError, KeyError) as e:
                log.warning(f"Corrupt session file: {path}: {e}")
    return None


def get_all_con_ids() -> set[int]:
    """Get con_ids of all current windows."""
    tree = swaymsg_json("-t", "get_tree")
    ids: set[int] = set()

    def walk(node):
        if node.get("app_id") or (node.get("window_properties") or {}).get("class"):
            con_id = node.get("id")
            if con_id:
                ids.add(con_id)
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            walk(child)

    walk(tree)
    return ids


def wait_for_new_window(known_ids: set[int]) -> int | None:
    """Wait for a new window to appear (not in known_ids)."""
    deadline = time.time() + WINDOW_TIMEOUT
    while time.time() < deadline:
        time.sleep(0.3)
        current_ids = get_all_con_ids()
        new_ids = current_ids - known_ids
        if new_ids:
            return next(iter(new_ids))
    return None


# --- VS Code の特別扱い ---------------------------------------------------
# VS Code は window.restoreWindows の既定値 "all" により、起動時に前回のウィンドウを
# 自分で復元する。ウィンドウごとに code を実行すると、2 回目以降は既に動いている
# インスタンスへの呼び出しとなり、空ウィンドウが増えていく。
# そのため起動は 1 回だけにして、本体が復元したウィンドウをタイトルで突き合わせ、
# 各ワークスペースへ移動して使う。

VSCODE_APP_IDS = {"code-oss", "code", "code-insiders"}
VSCODE_SETTLE_SECONDS = 2.0  # ウィンドウが増えなくなったと判断するまでの待ち時間

_vscode_pool: dict[int, str] = {}  # まだ配置していないウィンドウ con_id -> タイトル
_vscode_started = False


def vscode_workspace_name(title: str) -> str | None:
    """VS Code のウィンドウタイトルからワークスペース名を取り出す。
    例:「index.php - ナビス (Workspace) - Code - OSS」->「ナビス」
    ワークスペースを開いていないウィンドウでは None を返す。"""
    m = re.match(r"^(.*) \(Workspace\)(?: - .*)?$", title)
    if not m:
        return None
    return m.group(1).rsplit(" - ", 1)[-1].strip() or None


def get_windows_by_app(app_ids: set[str]) -> dict[int, str]:
    """指定した app_id のウィンドウを {con_id: タイトル} で返す。"""
    tree = swaymsg_json("-t", "get_tree")
    found: dict[int, str] = {}

    def walk(node):
        app_id = node.get("app_id") or (node.get("window_properties") or {}).get("class")
        con_id = node.get("id")
        if app_id in app_ids and con_id:
            found[con_id] = node.get("name") or ""
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            walk(child)

    walk(tree)
    return found


def ensure_vscode_windows():
    """VS Code を一度だけ起動し、本体が復元し終えたウィンドウを集める。"""
    global _vscode_started
    if _vscode_started:
        return
    _vscode_started = True

    if not get_windows_by_app(VSCODE_APP_IDS):
        if not command_exists("code"):
            log.warning("code コマンドが見つからない")
            return
        log.info("Launching: code (VS Code 本体の復元に任せる)")
        try:
            subprocess.Popen(
                ["code"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as e:
            log.error(f"Launch failed: code: {e}")
            return

    # ウィンドウが増えなくなるまで待つ
    deadline = time.time() + WINDOW_TIMEOUT
    stable_since = None
    windows: dict[int, str] = {}
    while time.time() < deadline:
        time.sleep(0.3)
        current = get_windows_by_app(VSCODE_APP_IDS)
        if current and current.keys() == windows.keys():
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= VSCODE_SETTLE_SECONDS:
                windows = current
                break
        else:
            stable_since = None
        windows = current

    _vscode_pool.clear()
    _vscode_pool.update(windows)
    log.info(
        f"VS Code ウィンドウを {len(_vscode_pool)} 個検出: "
        f"{list(_vscode_pool.values())}"
    )


def take_vscode_window(title: str) -> int | None:
    """保存しておいたタイトルに対応する VS Code ウィンドウを取り出す。"""
    if not _vscode_pool:
        return None

    # 1. タイトルが完全に一致するもの
    for con_id, name in list(_vscode_pool.items()):
        if name == title:
            del _vscode_pool[con_id]
            return con_id

    # 2. 同じワークスペースを開いているもの（開いているファイルは変わりうる）
    wanted = vscode_workspace_name(title)
    if wanted:
        for con_id, name in list(_vscode_pool.items()):
            if vscode_workspace_name(name) == wanted:
                del _vscode_pool[con_id]
                return con_id
        # 目的のワークスペースが復元されていないので、明示的に起動させる
        return None

    # 3. ワークスペースを開いていないウィンドウ同士を割り当てる
    for con_id, name in list(_vscode_pool.items()):
        if vscode_workspace_name(name) is None:
            del _vscode_pool[con_id]
            return con_id
    return None


def adopt_window(con_id: int) -> int:
    """既にあるウィンドウを、現在フォーカスしている位置へ移動する。"""
    swaymsg(f'[con_id="{con_id}"]', "move container to workspace current")
    time.sleep(0.2)
    return con_id


def launch_here(command: list[str], app_id: str, title: str = "") -> int | None:
    """Launch an app at the current focus position. Returns con_id.
    Sway automatically places new windows at the focused position."""
    if app_id in VSCODE_APP_IDS:
        ensure_vscode_windows()
        con_id = take_vscode_window(title)
        if con_id is not None:
            log.info(f"VS Code ウィンドウを再利用: con_id={con_id} title={title!r}")
            return adopt_window(con_id)
        log.info(f"対応する VS Code ウィンドウがないため起動する: {' '.join(command)}")

    if not command:
        log.warning(f"Empty command: {app_id}")
        return None

    cmd_name = command[0]
    base_cmd = os.path.basename(cmd_name)
    if not command_exists(cmd_name) and not command_exists(base_cmd):
        log.warning(f"Command not found: {cmd_name} ({app_id})")
        return None

    known_ids = get_all_con_ids()

    log.info(f"Launching: {' '.join(command)} ({app_id})")
    try:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        log.error(f"Launch failed: {command}: {e}")
        return None

    con_id = wait_for_new_window(known_ids)
    if con_id is None:
        log.warning(f"Window timeout: {app_id}")
    else:
        log.info(f"Window detected: {app_id} con_id={con_id}")
    return con_id


def get_first_leaf(node: dict) -> dict | None:
    """Get the first leaf (window) from a node tree."""
    if node["type"] == "window":
        return node
    elif node["type"] == "container":
        for child in node.get("nodes", []):
            leaf = get_first_leaf(child)
            if leaf:
                return leaf
    return None


def restore_subtree(node: dict, anchor_con_id: int,
                    placed: list[tuple[dict, int]]) -> int | None:
    """Build the remaining subtree with anchor_con_id as the first placed window.
    Returns the last con_id."""
    if node["type"] == "window":
        # This window was already launched as anchor
        return anchor_con_id

    elif node["type"] == "container":
        layout = node.get("layout", "splith")
        children = node.get("nodes", [])
        if not children:
            return None

        # Recursively process first child (anchor is the first leaf)
        first_con_id = restore_subtree(children[0], anchor_con_id, placed)
        if first_con_id is None:
            return None

        if len(children) == 1:
            return first_con_id

        # Set split direction
        swaymsg(f'[con_id="{anchor_con_id}"]', "focus")
        swaymsg(layout)

        last_con_id = first_con_id

        # Place remaining children
        for child in children[1:]:
            con_id = restore_node_full(child, placed)
            if con_id:
                last_con_id = con_id

        return last_con_id

    return None


def restore_node_full(node: dict, placed: list[tuple[dict, int]]) -> int | None:
    """Fully restore a node. Launch app at focus position and build structure."""
    if node["type"] == "window":
        command = node.get("command", [])
        app_id = node.get("app_id", "unknown")
        con_id = launch_here(command, app_id, node.get("title", ""))
        if con_id:
            placed.append((node, con_id))
        return con_id

    elif node["type"] == "container":
        layout = node.get("layout", "splith")
        children = node.get("nodes", [])
        if not children:
            return None

        # Place first child
        first_con_id = restore_node_full(children[0], placed)
        if first_con_id is None:
            return None

        if len(children) == 1:
            return first_con_id

        # Focus first child and set split direction
        swaymsg(f'[con_id="{first_con_id}"]', "focus")
        swaymsg(layout)

        last_con_id = first_con_id

        # Place remaining children
        for child in children[1:]:
            con_id = restore_node_full(child, placed)
            if con_id:
                last_con_id = con_id

        return last_con_id

    return None


def restore_workspace(ws: dict) -> list[tuple[dict, int]]:
    """Restore a workspace using 2-pass approach:
    Pass 1: Launch first window of each top-level node to establish split structure
    Pass 2: Build remaining subtrees within each top-level node"""
    name = ws["name"]
    log.info(f"Restoring workspace: {name}")

    swaymsg(f'workspace "{name}"')
    time.sleep(0.3)

    nodes = ws.get("nodes", [])
    floating = ws.get("floating_nodes", [])
    placed: list[tuple[dict, int]] = []

    if not nodes and not floating:
        return placed

    # === Pass 1: Establish top-level split structure ===
    anchor_ids: list[int | None] = []

    for i, node in enumerate(nodes):
        leaf = get_first_leaf(node)
        if leaf is None:
            anchor_ids.append(None)
            continue

        con_id = launch_here(leaf["command"], leaf["app_id"], leaf.get("title", ""))
        if con_id:
            placed.append((leaf, con_id))
        anchor_ids.append(con_id)

        # For 2+ top-level nodes: focus first anchor and set workspace layout
        if i == 0 and con_id and len(nodes) > 1:
            ws_layout = ws.get("layout", "splith")
            swaymsg(f'[con_id="{con_id}"]', "focus")
            swaymsg(ws_layout)

    log.info(f"Pass 1 complete: {len([a for a in anchor_ids if a])} anchors")

    # === Pass 2: Build subtrees within each top-level node ===
    for i, node in enumerate(nodes):
        anchor_id = anchor_ids[i]
        if anchor_id is None:
            continue

        swaymsg(f'[con_id="{anchor_id}"]', "focus")
        restore_subtree(node, anchor_id, placed)

    # Restore floating windows
    for fnode in floating:
        restore_floating(fnode)

    # Size restoration (top-down, ratio-based)
    log.info("Restoring sizes...")
    resize_tree(ws, placed)

    return placed


def find_con_id(node: dict, placed: list[tuple[dict, int]]) -> int | None:
    """Find con_id for a saved node from the placed list."""
    if node["type"] == "window":
        for wn, cid in placed:
            if wn is node:
                return cid
    return None


def collect_leaves(node: dict) -> list[dict]:
    """Collect all leaf windows under a node."""
    if node["type"] == "window":
        return [node]
    leaves = []
    for child in node.get("nodes", []):
        leaves.extend(collect_leaves(child))
    return leaves


def get_node_size(node: dict) -> tuple[int, int]:
    """Get node size. For containers, calculate from children."""
    if node["type"] == "window":
        return node.get("width", 0), node.get("height", 0)
    elif node["type"] == "container":
        layout = node.get("layout", "splith")
        children = node.get("nodes", [])
        if not children:
            return 0, 0
        if layout == "splith":
            total_w = sum(get_node_size(c)[0] for c in children)
            max_h = max((get_node_size(c)[1] for c in children), default=0)
            return total_w, max_h
        else:  # splitv
            max_w = max((get_node_size(c)[0] for c in children), default=0)
            total_h = sum(get_node_size(c)[1] for c in children)
            return max_w, total_h
    return 0, 0


def resize_tree(ws: dict, placed: list[tuple[dict, int]]):
    """Restore size ratios across the workspace tree (top-down).
    For each container, resize children to match their saved proportions."""
    nodes = ws.get("nodes", [])
    if len(nodes) < 2:
        for node in nodes:
            _resize_children(node, placed)
        return

    _resize_siblings(nodes, ws.get("layout", "splith"), placed)


def _resize_children(node: dict, placed: list[tuple[dict, int]]):
    """Recursively resize children within a container."""
    if node["type"] != "container":
        return
    children = node.get("nodes", [])
    if len(children) >= 2:
        _resize_siblings(children, node.get("layout", "splith"), placed)
    else:
        for child in children:
            _resize_children(child, placed)


def _resize_siblings(siblings: list[dict], layout: str,
                     placed: list[tuple[dict, int]]):
    """Restore size ratios between siblings.
    Top-down: resize siblings first, then process child containers."""
    # Resize all siblings except the last (it fills remaining space)
    is_horizontal = layout == "splith"
    for node in siblings[:-1]:
        target_w, target_h = get_node_size(node)
        leaves = collect_leaves(node)
        if not leaves:
            continue
        con_id = find_con_id(leaves[0], placed)
        if con_id is None:
            continue

        swaymsg(f'[con_id="{con_id}"]', "focus")
        if is_horizontal:
            swaymsg(f"resize set {target_w} px 0 px")
            log.info(f"Resize: con_id={con_id} width={target_w} (splith)")
        else:
            swaymsg(f"resize set 0 px {target_h} px")
            log.info(f"Resize: con_id={con_id} height={target_h} (splitv)")
        time.sleep(0.1)

    # Top-down: process child containers after sibling sizes are set
    for node in siblings:
        _resize_children(node, placed)


def restore_floating(window: dict) -> int | None:
    """Restore a floating window."""
    command = window.get("command", [])
    app_id = window.get("app_id", "unknown")
    con_id = launch_here(command, app_id, window.get("title", ""))
    if con_id is None:
        return None

    swaymsg(f'[con_id="{con_id}"]', "focus")
    swaymsg("floating enable")

    w = window.get("width", 0)
    h = window.get("height", 0)
    if w > 0 and h > 0:
        swaymsg(f"resize set {w} {h}")

    x = window.get("x", 0)
    y = window.get("y", 0)
    swaymsg(f"move position {x} {y}")

    return con_id


def restore_session():
    """Main: restore session."""
    session = load_session()
    if session is None:
        log.info("No session file found. Nothing to do.")
        return

    workspaces = session.get("workspaces", [])
    if not workspaces:
        log.info("Empty session. Nothing to do.")
        return

    focused_workspace = session.get("focused_workspace")
    log.info(f"Session restore started: {len(workspaces)} workspaces")

    for ws in workspaces:
        try:
            restore_workspace(ws)
        except Exception as e:
            log.error(f"Workspace restore failed: {ws.get('name')}: {e}")

    # セッションに対応がない VS Code ウィンドウが残っていれば知らせる
    # （勝手に閉じると未保存の内容を失うため、記録だけにとどめる）
    if _vscode_pool:
        log.warning(
            f"配置しなかった VS Code ウィンドウが {len(_vscode_pool)} 個残っている: "
            f"{list(_vscode_pool.values())}"
        )

    # Return to previously focused workspace
    if focused_workspace:
        time.sleep(0.2)
        swaymsg(f'workspace "{focused_workspace}"')
        log.info(f"Focus restored: {focused_workspace}")

    log.info("Session restore complete")


if __name__ == "__main__":
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        restore_session()
    except Exception as e:
        log.error(f"Restore error: {e}")
        sys.exit(1)
