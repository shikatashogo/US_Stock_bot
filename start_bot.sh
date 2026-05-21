#!/bin/bash
# US Stock Bot 自動再起動ラッパー
# クラッシュ時に自動で再起動することでIBKR接続断後のサイレント停止を防ぐ

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${SCRIPT_DIR}/venv/bin/python3"
LOGFILE="${SCRIPT_DIR}/logs/bot.log"
RESTART_WAIT=30  # 再起動前の待機秒数

cd "$SCRIPT_DIR"

echo "$(date '+%Y-%m-%d %H:%M:%S') [launcher] US Stock Bot 起動" >> "$LOGFILE"

while true; do
    echo "$(date '+%Y-%m-%d %H:%M:%S') [launcher] Bot プロセス開始" >> "$LOGFILE"
    "$PYTHON" main.py
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ]; then
        # 正常終了（KeyboardInterrupt等）はループを抜ける
        echo "$(date '+%Y-%m-%d %H:%M:%S') [launcher] Bot 正常終了 (exit=0). 再起動しません" >> "$LOGFILE"
        break
    fi

    echo "$(date '+%Y-%m-%d %H:%M:%S') [launcher] Bot 異常終了 (exit=$EXIT_CODE). ${RESTART_WAIT}秒後に再起動..." >> "$LOGFILE"
    sleep "$RESTART_WAIT"
done
