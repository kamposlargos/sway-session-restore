#!/bin/bash
# Swayセッション保存 → アプリ正常終了 → シャットダウン/リブート
# 使い方: sway-shutdown.sh [poweroff|reboot]

set -euo pipefail

ACTION="${1:-poweroff}"

if [[ "$ACTION" != "poweroff" && "$ACTION" != "reboot" ]]; then
    echo "使い方: $0 [poweroff|reboot]"
    exit 1
fi

# 1. セッション保存
echo "セッション保存中..."
~/.local/bin/sway-session-save.py --force 2>/dev/null || true

# 2. 全ウィンドウを正常に閉じる
echo "ウィンドウを閉じています..."
swaymsg -t get_tree | python3 -c "
import json, sys

def collect_con_ids(node):
    ids = []
    if node.get('app_id') or (node.get('window_properties') or {}).get('class'):
        con_id = node.get('id')
        if con_id:
            ids.append(con_id)
    for child in node.get('nodes', []) + node.get('floating_nodes', []):
        ids.extend(collect_con_ids(child))
    return ids

tree = json.load(sys.stdin)
for con_id in collect_con_ids(tree):
    print(con_id)
" | while read -r con_id; do
    swaymsg "[con_id=$con_id]" close 2>/dev/null || true
done

# 3. ウィンドウが閉じるのを待つ（最大10秒）
echo "ウィンドウの終了を待機中..."
for i in $(seq 1 20); do
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

# 4. シャットダウン/リブート
echo "${ACTION}を実行します..."
systemctl "$ACTION"
