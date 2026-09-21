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
        # (出現時刻, con_id, app_id, タイトル)
        self._reveal_at: list[tuple[float, int, str, str]] = []

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
        now = time.time()
        if base == "code":
            # VS Code 本体が前回のウィンドウを順次復元する様子を再現する
            self._reveal_at += [
                (now + 0.4, 10, "code-oss", "カンポス (Workspace) - Code - OSS"),
                (now + 0.9, 20, "code-oss",
                 "GMO診断設定.md - ICS研究所 (Workspace) - Code - OSS"),
                (now + 1.4, 30, "code-oss", "Code - OSS"),
            ]
        elif base.startswith("google-chrome") or base.startswith("chromium"):
            if any(a.startswith("--app=") for a in command):
                # PWA は 1 ウィンドウずつ確実に起動できるためプールの対象外
                self._add_window("chrome-tasks.google.com__-Profile_20",
                                 "ToDo リスト")
            else:
                # Chrome 本体が Profile 20 のウィンドウだけ復元する様子を再現する。
                # 保存されている 3 つのうち 2 つしか戻らない状況にしてある。
                # 2 つ目はタブを切り替えた想定でタイトルを変えてある。
                self._reveal_at += [
                    (now + 0.4, 40, "google-chrome",
                     "株式会社カンポスラルゴスムンディアレス - Google Chrome"),
                    (now + 0.9, 50, "google-chrome",
                     "ICS研究所のダッシュボード - Google Chrome"),
                ]
        else:
            self._add_window(base, base)
        return object()

    # -- 内部 --
    def _reveal_due(self):
        now = time.time()
        remaining = []
        for at, con_id, app_id, title in self._reveal_at:
            if now >= at:
                self.windows[con_id] = {
                    "app_id": app_id, "name": title, "workspace": "(未配置)"
                }
            else:
                remaining.append((at, con_id, app_id, title))
        self._reveal_at = remaining

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
    def window(app_id, title, command, width=857):
        return {"type": "window", "app_id": app_id, "id_source": "app_id",
                "command": command, "title": title, "width": width,
                "height": 1434, "floating": False, "focused": False}

    def code(title, command):
        return window("code-oss", title, command)

    def chrome(title):
        return window("google-chrome", title, ["google-chrome-stable"])

    def workspace(name, nodes, focused=False):
        return {"name": name, "output": "HEADLESS-1", "layout": "splith",
                "focused": focused, "floating_nodes": [], "nodes": nodes}

    return {
        "version": 1,
        "focused_workspace": "2",
        "workspaces": [
            workspace("1:KAMPOS", [
                window("com.mitchellh.ghostty", "~", ["ghostty"], width=827),
                chrome("株式会社カンポスラルゴスムンディアレス - Google Chrome"),
                code("カンポス (Workspace) - Code - OSS",
                     ["code", "/mnt/k/_workspace/カンポス.code-workspace"]),
            ]),
            workspace("2", [
                chrome("ネットde診断 - Google Chrome"),
                code("ICS研究所 (Workspace) - Code - OSS",
                     ["code", "/mnt/k/_workspace/ICS研究所.code-workspace"]),
            ], focused=True),
            workspace("3", [
                # Chrome が復元しない分（別プロファイル想定）
                chrome("LINE Official Account Manager - Google Chrome"),
                code("Code - OSS", ["code"]),
            ]),
            workspace("10:ToDo", [
                window("chrome-tasks.google.com__-Profile_20", "ToDo リスト",
                       ["google-chrome-stable", "--app=https://tasks.google.com",
                        "--profile-directory=Profile 20"]),
            ]),
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
    print("起動されたコマンド:")
    for c in fake.launches:
        print(f"  {c}")

    code_launches = [c for c in fake.launches if Path(c[0]).name == "code"]
    chrome_bare = [c for c in fake.launches
                   if Path(c[0]).name.startswith("google-chrome") and len(c) == 1]
    chrome_app = [c for c in fake.launches
                  if any(a.startswith("--app=") for a in c)]
    print()
    print(f"  code の起動回数            : {len(code_launches)}")
    print(f"  引数なし Chrome の起動回数 : {len(chrome_bare)}")
    print(f"  PWA の起動回数             : {len(chrome_app)}")

    print()
    print("ウィンドウの配置先:")
    for con_id, w in sorted(fake.windows.items()):
        print(f"  con_id={con_id:<4} workspace={w['workspace']!r:<12} "
              f"app_id={w['app_id']}")
        print(f"        title={w['name']!r}")

    print()
    print("=== 判定 ===")
    ok = True

    def check(cond, ok_msg, ng_msg):
        nonlocal ok
        if cond:
            print(f"  OK: {ok_msg}")
        else:
            print(f"  NG: {ng_msg}")
            ok = False

    check(len(code_launches) == 1, "code の起動は 1 回だけ",
          f"code の起動が {len(code_launches)} 回（期待は 1 回）")
    check(len(chrome_bare) == 1, "引数なし Chrome の起動は 1 回だけ",
          f"引数なし Chrome の起動が {len(chrome_bare)} 回（期待は 1 回）")
    check(len(chrome_app) == 1, "PWA は従来どおり個別に起動",
          f"PWA の起動が {len(chrome_app)} 回（期待は 1 回）")

    expected = {
        10: ("1:KAMPOS", "VS Code カンポス"),
        20: ("2", "VS Code ICS研究所（タイトル変化分）"),
        30: ("3", "VS Code 空ウィンドウ"),
        40: ("1:KAMPOS", "Chrome タイトル一致"),
        50: ("2", "Chrome タイトル変化分の繰り上げ"),
    }
    for con_id, (want, label) in expected.items():
        got = fake.windows.get(con_id, {}).get("workspace")
        check(got == want, f"{label}: con_id={con_id} -> {want}",
              f"{label}: con_id={con_id} -> {got!r}（期待は {want!r}）")

    leftovers = [c for c, w in fake.windows.items()
                 if w["workspace"] == "(未配置)"]
    check(not leftovers, "未配置のウィンドウなし",
          f"未配置のウィンドウ {leftovers}")

    print()
    print("総合判定:", "合格" if ok else "不合格")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
