#!/usr/bin/env python3
"""sway-session-restore.py の VS Code 復元処理を、実機に触れずに検証する。

swaymsg 呼び出しとプロセス起動を差し替え、仮想的なウィンドウ木で復元を実行する。
確認したいこと:
  1. code の起動が 1 回だけであること（ウィンドウごとに起動すると空ウィンドウが増える）
  2. VS Code 本体が復元した 3 ウィンドウが、保存時のワークスペースへ割り当てられること
  3. VS Code 以外のアプリは従来どおり起動されること
"""

import importlib.util
import json
import logging
import time
from pathlib import Path

import sys
import tempfile

# 第 1 引数でテスト対象を差し替えられる（改修前後の比較に使う）
# 既定は同じディレクトリにある sway-session-restore.py
RESTORE_PY = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    Path(__file__).resolve().parent / "sway-session-restore.py"
)
# テスト用セッションの置き場（スクリプトの隣を汚さない）
TMP = Path(tempfile.mkdtemp(prefix="sway-session-test-"))


def load_restore_module():
    spec = importlib.util.spec_from_file_location("restore_under_test", RESTORE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- 仮想ウィンドウ木 -----------------------------------------------------

class FakeSway:
    """swaymsg の代わりに、仮想的なウィンドウ木への操作を受け取る。"""

    def __init__(self):
        self.windows: dict[int, dict] = {}   # con_id -> {app_id, name, workspace}
        self.commands: list[str] = []        # 受け取った swaymsg の内容
        self.launches: list[list[str]] = []  # 起動されたコマンド
        self.current_workspace = ""
        self._next_id = 100
        self._vscode_reveal_at: list[tuple[float, int, str]] = []

    # -- swaymsg の差し替え先 --
    def swaymsg(self, *args) -> str:
        line = " ".join(args)
        self.commands.append(line)
        if line.startswith("workspace "):
            self.current_workspace = line.split(" ", 1)[1].strip('"')
        elif "move container to workspace current" in line:
            con_id = int(line.split('con_id="')[1].split('"')[0])
            if con_id in self.windows:
                self.windows[con_id]["workspace"] = self.current_workspace
        return ""

    def swaymsg_json(self, *args):
        if args[:2] == ("-t", "get_tree"):
            self._reveal_due()
            return self._tree()
        return {}

    # -- プロセス起動の差し替え先 --
    def popen(self, command, **kwargs):
        self.launches.append(list(command))
        base = Path(command[0]).name
        if base == "code":
            # VS Code 本体が前回のウィンドウを順次復元する様子を再現する
            now = time.time()
            self._vscode_reveal_at = [
                (now + 0.4, 10, "カンポス (Workspace) - Code - OSS"),
                (now + 0.9, 20, "GMO診断設定.md - ICS研究所 (Workspace) - Code - OSS"),
                (now + 1.4, 30, "Code - OSS"),
            ]
        else:
            self._add_window(base, base)
        return object()

    # -- 内部 --
    def _reveal_due(self):
        now = time.time()
        remaining = []
        for at, con_id, title in self._vscode_reveal_at:
            if now >= at:
                self.windows[con_id] = {
                    "app_id": "code-oss", "name": title, "workspace": "(未配置)"
                }
            else:
                remaining.append((at, con_id, title))
        self._vscode_reveal_at = remaining

    def _add_window(self, app_id: str, name: str) -> int:
        con_id = self._next_id
        self._next_id += 1
        self.windows[con_id] = {
            "app_id": app_id, "name": name, "workspace": self.current_workspace
        }
        return con_id

    def _tree(self) -> dict:
        by_ws: dict[str, list] = {}
        for con_id, w in self.windows.items():
            by_ws.setdefault(w["workspace"], []).append({
                "type": "con", "id": con_id, "app_id": w["app_id"],
                "name": w["name"], "nodes": [], "floating_nodes": [],
            })
        return {
            "type": "root", "nodes": [{
                "type": "output", "name": "HEADLESS-1", "nodes": [
                    {"type": "workspace", "name": ws, "nodes": nodes,
                     "floating_nodes": []}
                    for ws, nodes in by_ws.items()
                ],
                "floating_nodes": [],
            }],
            "floating_nodes": [],
        }


# --- テスト用セッション ---------------------------------------------------

def build_session() -> dict:
    def code_window(title, command, width=1714):
        return {"type": "window", "app_id": "code-oss", "id_source": "app_id",
                "command": command, "title": title, "width": width,
                "height": 1434, "floating": False, "focused": False}

    return {
        "version": 1,
        "focused_workspace": "2",
        "workspaces": [
            {"name": "1:KAMPOS", "output": "HEADLESS-1", "layout": "splith",
             "focused": False, "floating_nodes": [], "nodes": [
                 {"type": "window", "app_id": "com.mitchellh.ghostty",
                  "id_source": "app_id", "command": ["ghostty"], "title": "~",
                  "width": 827, "height": 1434, "floating": False,
                  "focused": False},
                 code_window("カンポス (Workspace) - Code - OSS",
                             ["code", "/mnt/k/_workspace/カンポス.code-workspace"]),
             ]},
            {"name": "2", "output": "HEADLESS-1", "layout": "splith",
             "focused": True, "floating_nodes": [], "nodes": [
                 code_window("ICS研究所 (Workspace) - Code - OSS",
                             ["code", "/mnt/k/_workspace/ICS研究所.code-workspace"]),
             ]},
            {"name": "3", "output": "HEADLESS-1", "layout": "splith",
             "focused": False, "floating_nodes": [], "nodes": [
                 code_window("Code - OSS", ["code"]),
             ]},
        ],
    }


# --- 実行 -----------------------------------------------------------------

def main():
    module = load_restore_module()

    # 本物のログファイルを汚さないよう、ハンドラを差し替える
    logging.getLogger().handlers.clear()
    logging.basicConfig(level=logging.INFO, format="    %(message)s")

    fake = FakeSway()
    module.swaymsg = fake.swaymsg
    module.swaymsg_json = fake.swaymsg_json
    module.command_exists = lambda cmd: True
    module.subprocess.Popen = fake.popen

    session_file = TMP / "test-session.json"
    session_file.write_text(json.dumps(build_session(), ensure_ascii=False),
                            encoding="utf-8")
    module.SAVE_FILE = session_file
    module.BACKUP_FILE = session_file

    print("=== 復元処理の実行ログ ===")
    module.restore_session()

    print()
    print("=== 結果 ===")
    print(f"起動されたコマンド: {fake.launches}")
    code_launches = [c for c in fake.launches if Path(c[0]).name == "code"]
    print(f"  うち code の起動回数: {len(code_launches)}")
    print()
    print("VS Code ウィンドウの配置先:")
    for con_id, w in sorted(fake.windows.items()):
        if w["app_id"] == "code-oss":
            print(f"  con_id={con_id}  workspace={w['workspace']!r}  "
                  f"title={w['name']!r}")
    print()
    print("その他のウィンドウ:")
    for con_id, w in sorted(fake.windows.items()):
        if w["app_id"] != "code-oss":
            print(f"  con_id={con_id}  workspace={w['workspace']!r}  "
                  f"app_id={w['app_id']}")

    print()
    print("=== 判定 ===")
    expected = {10: "1:KAMPOS", 20: "2", 30: "3"}
    ok = True
    if len(code_launches) != 1:
        print(f"  NG: code の起動が {len(code_launches)} 回（期待は 1 回）")
        ok = False
    else:
        print("  OK: code の起動は 1 回だけ")
    for con_id, want in expected.items():
        got = fake.windows.get(con_id, {}).get("workspace")
        if got == want:
            print(f"  OK: con_id={con_id} -> {want}")
        else:
            print(f"  NG: con_id={con_id} -> {got!r}（期待は {want!r}）")
            ok = False
    leftovers = [c for c, w in fake.windows.items()
                 if w["app_id"] == "code-oss" and w["workspace"] == "(未配置)"]
    if leftovers:
        print(f"  NG: 未配置の VS Code ウィンドウ {leftovers}")
        ok = False
    else:
        print("  OK: 未配置の VS Code ウィンドウなし")
    print()
    print("総合判定:", "合格" if ok else "不合格")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
