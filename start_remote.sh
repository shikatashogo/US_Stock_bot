#!/bin/bash
# =====================================================
# 株式推奨Bot リモートアクセス起動スクリプト
# =====================================================
# 使い方:
#   ./start_remote.sh
#
# スマホからのアクセスURL（Tailscale IP確認後）:
#   http://[TailscaleのIPアドレス]:8501
#   例: http://100.64.0.1:8501
# =====================================================

cd "$(dirname "$0")"

# Tailscale IPアドレスを自動取得
TAILSCALE_IP=$(tailscale ip -4 2>/dev/null)
LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)

echo "========================================"
echo "  📊 株式推奨Bot 起動中..."
echo "========================================"
echo ""
echo "【アクセスURL】"
echo "  🏠 自宅Wi-Fi:    http://${LOCAL_IP:-[Wi-Fi IP]}:8501"
if [ -n "$TAILSCALE_IP" ]; then
    echo "  📱 スマホ(外出先): http://${TAILSCALE_IP}:8501"
else
    echo "  📱 スマホ(外出先): Tailscaleが起動していません"
    echo "             → メニューバーのTailscaleアイコンをONにしてください"
fi
echo ""
echo "【停止方法】 Ctrl+C を押す"
echo "========================================"
echo ""

# Streamlit起動（全インターフェースでリッスン）
source venv/bin/activate 2>/dev/null || true
venv/bin/streamlit run app.py \
    --server.address=0.0.0.0 \
    --server.port=8501 \
    --server.headless=true
