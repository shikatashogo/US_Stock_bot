"""
通知モジュール（Telegram Bot使用）
LINE Notifyは2025年3月31日にサービス終了のためTelegramに変更

設定方法:
1. Telegramアプリで @BotFather を開く
2. /newbot でBotを作成 → TOKENを取得
3. 自分のBotにメッセージを送ってから
   https://api.telegram.org/botTOKEN/getUpdates にアクセスして chat_id を取得
4. config/.env に TELEGRAM_BOT_TOKEN と TELEGRAM_CHAT_ID を設定
"""
import os
import requests
from typing import Optional, Dict, List
from datetime import datetime
import pytz
from loguru import logger


class LineNotifier:
    """Telegram Bot APIを使用して通知を送信するクラス（LINE Notify代替）"""

    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")

        if not self.token or not self.chat_id:
            logger.warning("TELEGRAM_BOT_TOKEN または TELEGRAM_CHAT_ID が未設定です。通知は無効です")

        self.et_tz = pytz.timezone("America/New_York")
        self.jst_tz = pytz.timezone("Asia/Tokyo")
        self.api_url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    def send(self, message: str) -> bool:
        """テキストメッセージを送信する"""
        if not self.token or not self.chat_id:
            logger.debug(f"[通知スキップ] {message[:50]}...")
            return False

        try:
            response = requests.post(
                self.api_url,
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                },
                timeout=10,
            )
            if response.status_code == 200:
                logger.debug("Telegram通知送信成功")
                return True
            else:
                logger.error(f"Telegram通知失敗: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Telegram通知エラー: {e}")
            return False

    def notify_trade_entry(self, trade: Dict) -> bool:
        """エントリー（買い）の通知を送信する"""
        jst_now = datetime.now(self.jst_tz).strftime("%H:%M")
        stop_pct = (trade['entry_price'] - trade['stop_loss']) / trade['entry_price'] * 100
        message = (
            f"🟢 <b>エントリー通知</b> [{jst_now} JST]\n"
            f"銘柄: {trade['symbol']}\n"
            f"戦略: {trade['strategy']}\n"
            f"株数: {trade['shares']}株\n"
            f"エントリー: ${trade['entry_price']:.2f}\n"
            f"損切り: ${trade['stop_loss']:.2f} (-{stop_pct:.1f}%)\n"
            f"利確目標: ${trade['take_profit']:.2f}\n"
            f"理由: {trade.get('signal_reason', 'N/A')[:100]}"
        )
        return self.send(message)

    def notify_trade_exit(self, trade: Dict, pnl: float, exit_reason: str) -> bool:
        """エグジット（売り/決済）の通知を送信する"""
        jst_now = datetime.now(self.jst_tz).strftime("%H:%M")
        pnl_emoji = "✅" if pnl >= 0 else "❌"
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        message = (
            f"{pnl_emoji} <b>エグジット通知</b> [{jst_now} JST]\n"
            f"銘柄: {trade['symbol']}\n"
            f"損益: {pnl_str}\n"
            f"理由: {exit_reason}"
        )
        return self.send(message)

    def notify_emergency_stop(self, reason: str, portfolio_value: float) -> bool:
        """緊急停止（損失上限到達等）の通知を送信する"""
        jst_now = datetime.now(self.jst_tz).strftime("%Y/%m/%d %H:%M")
        message = (
            f"⛔ <b>緊急停止アラート</b> [{jst_now}]\n"
            f"理由: {reason}\n"
            f"現在資産: ${portfolio_value:.2f}\n"
            f"Botは自動停止しました。\n"
            f"IBKRを確認してポジションを手動で確認してください。"
        )
        return self.send(message)

    def notify_daily_report(self, report: Dict) -> bool:
        """日次レポートを送信する"""
        jst_today = datetime.now(self.jst_tz).strftime("%Y/%m/%d")

        # 取引損益（DBベース・為替含まず）
        trade_pnl = report.get("daily_pnl", 0)
        trade_emoji = "📈" if trade_pnl >= 0 else "📉"
        trade_str = f"+${trade_pnl:.2f}" if trade_pnl >= 0 else f"-${abs(trade_pnl):.2f}"

        # 累計損益
        monthly_pnl = report.get("monthly_pnl", 0)
        yearly_pnl = report.get("yearly_pnl", 0)
        monthly_str = f"+${monthly_pnl:.2f}" if monthly_pnl >= 0 else f"-${abs(monthly_pnl):.2f}"
        yearly_str = f"+${yearly_pnl:.2f}" if yearly_pnl >= 0 else f"-${abs(yearly_pnl):.2f}"

        jst_now = datetime.now(self.jst_tz).strftime("%H:%M")
        lines = [
            f"{trade_emoji} <b>日次レポート [{jst_today}]</b>",
            f"集計時刻: {jst_now} JST",
            f"{'─'*28}",
            f"<b>本日の取引損益:</b> {trade_str}",
            f"ポートフォリオ: ${report.get('portfolio_value', 0):,.2f}",
            f"",
            f"<b>累計損益</b>",
            f"当月: {monthly_str}",
            f"当年: {yearly_str}",
            f"",
            f"<b>取引サマリー</b>",
            f"取引数: {report.get('trades_count', 0)}回",
            f"勝ち: {report.get('winning_trades', 0)}回 / 負け: {report.get('losing_trades', 0)}回",
            f"勝率: {report.get('win_rate', 0):.0f}%",
        ]

        if report.get("best_trade"):
            bt = report["best_trade"]
            lines.append(f"🏆 最優秀: {bt['symbol']} +${bt['pnl']:.2f}")

        if report.get("worst_trade"):
            wt = report["worst_trade"]
            lines.append(f"💀 最大損失: {wt['symbol']} -${abs(wt['pnl']):.2f}")

        if report.get("reflections"):
            lines.append(f"")
            lines.append(f"<b>本日の反省点</b>")
            for r in report["reflections"][:3]:
                lines.append(f"• {r}")

        if report.get("tomorrow_watch"):
            lines.append(f"")
            lines.append(f"<b>明日の注目銘柄</b>")
            for w in report["tomorrow_watch"][:3]:
                lines.append(f"• {w}")

        return self.send("\n".join(lines))

    def notify_strategy_update(self, changes: List[str]) -> bool:
        """戦略更新の通知を送信する"""
        jst_now = datetime.now(self.jst_tz).strftime("%Y/%m/%d %H:%M")
        lines = [f"🔧 <b>戦略更新通知</b> [{jst_now}]"]
        for change in changes:
            lines.append(f"• {change}")
        return self.send("\n".join(lines))

    def notify_market_open(self, market_status: Dict) -> bool:
        """市場オープン時の通知を送信する"""
        vix = market_status.get("vix", 0)
        spy_trend = market_status.get("spy_trend", "neutral")
        condition = market_status.get("market_condition", "unknown")
        trend_emoji = {"up": "📈", "down": "📉", "neutral": "➡️"}.get(spy_trend, "❓")
        jst_now = datetime.now(self.jst_tz).strftime("%H:%M")
        message = (
            f"🔔 <b>米国市場オープン</b> [{jst_now} JST]\n"
            f"市場状況: {condition}\n"
            f"SPYトレンド: {trend_emoji} {spy_trend}\n"
            f"VIX: {vix:.1f}\n"
            f"自動取引を開始します..."
        )
        return self.send(message)

    def notify_market_close(self) -> bool:
        """市場クローズ時の通知を送信する（レポートは別途 notify_daily_report で送信）"""
        jst_now = datetime.now(self.jst_tz).strftime("%H:%M")
        message = (
            f"🔕 <b>米国市場クローズ</b> [{jst_now} JST]\n"
            f"自動取引を終了しました。日次レポートを送信します。"
        )
        return self.send(message)

    def notify_connection_lost(self) -> bool:
        """IBKR接続断の通知を送信する"""
        jst_now = datetime.now(self.jst_tz).strftime("%H:%M")
        message = (
            f"⚠️ <b>接続断アラート</b> [{jst_now}]\n"
            f"IBKRとの接続が切れました。\n"
            f"再接続を試みています..."
        )
        return self.send(message)
