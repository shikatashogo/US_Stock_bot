#!/bin/bash
# 株式推奨Bot Web UI 起動スクリプト
# ターミナルで ./start_app.sh を実行するだけで起動

cd "$(dirname "$0")"
source venv/bin/activate

echo "📊 株式推奨Bot を起動しています..."
echo "ブラウザが自動で開きます → http://localhost:8501"
echo "終了するには Ctrl+C"
echo ""

streamlit run app.py --server.port 8501
