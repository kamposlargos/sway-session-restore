#!/bin/bash
# Swayセッション保存 → アプリ正常終了 → シャットダウン/リブート
# 使い方: sway-shutdown.sh [poweroff|reboot]
#
# ターミナルから実行された場合、自動的にswaymsg exec経由で
# 再実行し、ターミナルに依存しない形で動作する

set -euo pipefail

ACTION="${1:-poweroff}"

if [[ "$ACTION" != "poweroff" && "$ACTION" != "reboot" ]]; then
    echo "使い方: $0 [poweroff|reboot]"
    exit 1
fi

# blockインヒビターのチェック（デタッチ前にターミナル上で確認）
check_inhibitors() {
    local blockers
    blockers=$(systemd-inhibit --list --no-pager 2>/dev/null | grep -v '^WHO' | grep 'shutdown' | grep 'block' || true)
    if [ -n "$blockers" ]; then
        echo "シャットダウンをブロックしているプロセスがあります:"
        echo "$blockers"
        notify-send -u critical "シャットダウン中止" "ブロックしているプロセスがあります" 2>/dev/null || true
        return 1
    fi
    return 0
}

# ターミナルから実行された場合、インヒビターチェック後にswaymsg exec経由で再実行
# （ターミナルが閉じてもスクリプトが中断しないようにする）
if [ -z "${_SWAY_SHUTDOWN_DETACHED:-}" ]; then
    check_inhibitors || exit 1
    SCRIPT_PATH="$(realpath "$0")"
    swaymsg "exec env _SWAY_SHUTDOWN_DETACHED=1 $SCRIPT_PATH $ACTION"
    exit 0
fi

LOGFILE="/tmp/sway-shutdown-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOGFILE") 2>&1

# デタッチ後の再チェック（デタッチ前チェックからの時間差で新たにインヒビターが発生した場合）
check_inhibitors || exit 1

echo "${ACTION}を開始します..."
notify-send "シャットダウン" "${ACTION}を開始します" 2>/dev/null || true

TERMINALS='com.mitchellh.ghostty Alacritty foot kitty wezterm'
BROWSERS='google-chrome Google-chrome chromium Chromium firefox Firefox'
# Electron 系の重いアプリ。ウィンドウ消滅後も LSP・拡張機能ホスト等の子プロセスが
# 残り、それらが /mnt 配下に cwd を持つとアンマウントを妨げるため完全終了を待つ
HEAVY_APPS='code code-oss codium Code Code-OSS VSCodium cursor Cursor'

is_terminal() {
    local app="$1"
    for t in $TERMINALS; do
        [ "$app" = "$t" ] && return 0
    done
    return 1
}

is_browser() {
    local app="$1"
    for b in $BROWSERS; do
        [ "$app" = "$b" ] && return 0
    done
    # Chrome PWA
    [[ "$app" == chrome-* ]] && return 0
    return 1
}

is_heavy() {
    local app="$1"
    for h in $HEAVY_APPS; do
        [ "$app" = "$h" ] && return 0
    done
    return 1
}

# 1. セッション保存
echo "セッション保存中..."
~/.local/bin/sway-session-save.py --force 2>/dev/null || true

# 2. ウィンドウ情報を収集（PID付き）
collect_windows() {
    swaymsg -t get_tree | python3 -c "
import json, sys

def collect(node):
    windows = []
    if node.get('app_id') or (node.get('window_properties') or {}).get('class'):
        app = node.get('app_id') or (node.get('window_properties') or {}).get('class', '')
        con_id = node.get('id')
        pid = node.get('pid', 0)
        if con_id:
            windows.append((app, con_id, pid))
    for child in node.get('nodes', []) + node.get('floating_nodes', []):
        windows.extend(collect(child))
    return windows

tree = json.load(sys.stdin)
for app, con_id, pid in collect(tree):
    print(f'{app}\t{con_id}\t{pid}')
"
}

# 3. ターミナルの子プロセスツリーを再帰的にSIGTERMで終了
kill_process_tree() {
    local pid="$1"
    local children
    children=$(pgrep -P "$pid" 2>/dev/null || true)
    for child in $children; do
        kill_process_tree "$child"
    done
    kill -TERM "$pid" 2>/dev/null || true
}

kill_terminal_children() {
    local term_pid="$1"
    local children
    children=$(pgrep -P "$term_pid" 2>/dev/null || true)
    for child in $children; do
        echo "    子プロセス終了: pid=$child ($(cat /proc/$child/comm 2>/dev/null || echo '?'))"
        kill_process_tree "$child"
    done
}

wait_pid_gone() {
    local pid="$1"
    local max_wait="${2:-10}"
    local i=0
    while [ "$i" -lt "$max_wait" ] && [ -d "/proc/$pid" ]; do
        sleep 0.5
        i=$((i + 1))
    done
}

# 4. ウィンドウを分類して順番に閉じる
normal_windows=""
heavy_windows=""
browser_windows=""
terminal_windows=""

while IFS=$'\t' read -r app con_id pid; do
    if is_terminal "$app"; then
        terminal_windows="${terminal_windows}${app}\t${con_id}\t${pid}\n"
    elif is_browser "$app"; then
        browser_windows="${browser_windows}${app}\t${con_id}\t${pid}\n"
    elif is_heavy "$app"; then
        heavy_windows="${heavy_windows}${app}\t${con_id}\t${pid}\n"
    else
        normal_windows="${normal_windows}${app}\t${con_id}\t${pid}\n"
    fi
done < <(collect_windows)

# 4a. 通常アプリを閉じる
if [ -n "$normal_windows" ]; then
    echo "通常アプリを閉じています..."
    echo -e "$normal_windows" | while IFS=$'\t' read -r app con_id pid; do
        [ -z "$app" ] && continue
        echo "  閉じています: $app (con_id=$con_id)"
        swaymsg "[con_id=$con_id]" kill 2>/dev/null || true
        sleep 0.2
    done
fi

# 4b. ブラウザを閉じる
if [ -n "$browser_windows" ]; then
    echo "ブラウザを閉じています..."
    echo -e "$browser_windows" | while IFS=$'\t' read -r app con_id pid; do
        [ -z "$app" ] && continue
        echo "  閉じています: $app (con_id=$con_id)"
        swaymsg "[con_id=$con_id]" kill 2>/dev/null || true
        sleep 0.3
    done
    # ブラウザプロセスの完全終了を待つ（セッション保存の完了を保証）
    sleep 1
    for proc in chrome chromium firefox; do
        local_pid=$(pgrep -x "$proc" -o 2>/dev/null || true)
        if [ -n "$local_pid" ]; then
            echo "  ${proc}の終了を待機中..."
            wait_pid_gone "$local_pid" 20
        fi
    done
fi

# 4b-2. Electron 系の重いアプリ（VSCode/Cursor等）を閉じて完全終了を待つ
# ウィンドウが消えても LSP・拡張機能ホスト等が残り、それらが /mnt 配下に
# cwd を持つとアンマウントを妨げるため、プロセスツリー終了まで待つ
if [ -n "$heavy_windows" ]; then
    echo "重いアプリ（Electron系）を閉じています..."
    echo -e "$heavy_windows" | while IFS=$'\t' read -r app con_id pid; do
        [ -z "$app" ] && continue
        echo "  閉じています: $app (con_id=$con_id, pid=$pid)"
        swaymsg "[con_id=$con_id]" kill 2>/dev/null || true
        sleep 0.3
    done
    echo -e "$heavy_windows" | while IFS=$'\t' read -r app con_id pid; do
        [ -z "$app" ] && continue
        if [ "$pid" -gt 0 ] && [ -d "/proc/$pid" ]; then
            echo "  ${app} (pid=$pid) の終了を待機中..."
            wait_pid_gone "$pid" 30
            if [ -d "/proc/$pid" ]; then
                echo "    プロセスツリーに SIGTERM 送信: pid=$pid"
                kill_process_tree "$pid"
                wait_pid_gone "$pid" 10
            fi
        fi
    done
fi

# 4c. ターミナルの子プロセスを終了してからターミナルを閉じる
if [ -n "$terminal_windows" ]; then
    echo "ターミナルを閉じています..."
    echo -e "$terminal_windows" | while IFS=$'\t' read -r app con_id pid; do
        [ -z "$app" ] && continue
        if [ "$pid" -gt 0 ]; then
            echo "  子プロセスを終了中: $app (pid=$pid)"
            kill_terminal_children "$pid"
            wait_pid_gone "$pid" 10
        fi
        echo "  閉じています: $app (con_id=$con_id)"
        swaymsg "[con_id=$con_id]" kill 2>/dev/null || true
        sleep 0.3
    done
fi

# 5. 全ウィンドウの終了を待つ（最大15秒）
echo "全ウィンドウの終了を待機中..."
for i in $(seq 1 30); do
    count=$(swaymsg -t get_tree | python3 -c "
import json, sys
def count_windows(node):
    c = 0
    if node.get('app_id') or (node.get('window_properties') or {}).get('class'):
        c = 1
    for child in node.get('nodes', []) + node.get('floating_nodes', []):
        c += count_windows(child)
    return c
print(count_windows(json.load(sys.stdin)))
" 2>/dev/null || echo "0")
    if [ "$count" -eq 0 ]; then
        echo "全ウィンドウが終了しました"
        break
    fi
    sleep 0.5
done

# 6. シャットダウン/リブート
echo "${ACTION}を実行します..."
systemctl "$ACTION"
