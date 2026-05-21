#!/bin/bash
# ============================================================
# US Stock Trading Bot - セットアップスクリプト
# 初回起動前に必ずこのスクリプトを実行してください
# ============================================================

set -e

echo "============================================================"
echo " US Stock Trading Bot - セットアップ開始"
echo "============================================================"

# Python バージョンチェック
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

echo ""
echo "Pythonバージョン: $PYTHON_VERSION"

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]); then
    echo "⚠️  Python 3.11以上が必要です"
    echo "   pyenv または公式サイトからPythonをインストールしてください"
    exit 1
fi

echo "✅ Python バージョンOK"

# 仮想環境の作成
echo ""
echo "仮想環境を作成中..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✅ 仮想環境 (venv) 作成完了"
else
    echo "ℹ️  仮想環境は既に存在します"
fi

# 仮想環境をアクティベート
source venv/bin/activate
echo "✅ 仮想環境アクティベート"

# pip アップデート
echo ""
echo "pipをアップデート中..."
pip install --upgrade pip -q
echo "✅ pip アップデート完了"

# 依存ライブラリのインストール
echo ""
echo "依存ライブラリをインストール中（数分かかる場合があります）..."
pip install -r requirements.txt -q
echo "✅ 依存ライブラリインストール完了"

# 必要なディレクトリの作成
echo ""
echo "ディレクトリを確認・作成中..."
mkdir -p logs data/historical reports

echo "✅ ディレクトリ作成完了"

# .envファイルの確認
echo ""
if [ ! -f "config/.env" ]; then
    echo "環境変数ファイルを作成中..."
    cp config/.env.example config/.env
    echo ""
    echo "⚠️  重要: config/.env ファイルに以下の情報を入力してください:"
    echo "   1. LINE_NOTIFY_TOKEN  → LINE Notify のトークン"
    echo "      取得: https://notify-bot.line.me/ja/"
    echo ""
    echo "   2. ANTHROPIC_API_KEY  → Claude API キー（オプション）"
    echo "      取得: https://console.anthropic.com/"
    echo ""
    echo "   3. IBKR_ACCOUNT       → IBKR口座番号"
    echo ""
else
    echo "ℹ️  config/.env は既に存在します"
fi

# セットアップ完了
echo ""
echo "============================================================"
echo " セットアップ完了！"
echo "============================================================"
echo ""
echo "次のステップ:"
echo ""
echo "1. IBKRのTWS（Trader Workstation）またはIB Gatewayを起動"
echo "   ポート: 7497（ペーパートレード）/ 7496（本番）"
echo ""
echo "2. 環境変数を設定"
echo "   nano config/.env"
echo ""
echo "3. バックテストで戦略を確認"
echo "   source venv/bin/activate"
echo "   python main.py --backtest"
echo ""
echo "4. ペーパートレードで動作確認"
echo "   python main.py"
echo ""
echo "5. 問題なければ本番取引開始"
echo "   python main.py --live"
echo ""
echo "============================================================"
