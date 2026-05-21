"""
Telegram通知モジュール
======================
推奨レポートをTelegramに送信する。

セットアップ手順:
  1. Telegramで @BotFather を開く
  2. /newbot → Bot名・ユーザー名を設定 → トークンを取得
  3. 自分のBot（@yourbot）にメッセージを送る
  4. https://api.telegram.org/bot<TOKEN>/getUpdates でchat_idを確認
  5. .env に TELEGRAM_BOT_TOKEN と TELEGRAM_CHAT_ID を設定

料金: 完全無料（Telegram Bot API に利用料なし）
"""
from __future__ import annotations

import os
from typing import Optional

import requests
from loguru import logger

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramNotifier:
    """Telegram Bot経由でメッセージを送信するクラス"""

    def __init__(
        self,
        token:   Optional[str] = None,
        chat_id: Optional[str] = None,
    ):
        self.token   = token   or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

    def is_configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        テキストメッセージを送信する

        Args:
            text      : 送信するテキスト（HTML or Markdown対応）
            parse_mode: "HTML" or "Markdown"
        Returns:
            True = 送信成功
        """
        if not self.is_configured():
            logger.warning(
                "Telegram未設定。.env に TELEGRAM_BOT_TOKEN と "
                "TELEGRAM_CHAT_ID を設定してください。"
            )
            return False

        url = TELEGRAM_API.format(token=self.token, method="sendMessage")
        payload = {
            "chat_id":    self.chat_id,
            "text":       text[:4096],  # Telegram上限
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.info("Telegram送信成功")
                return True
            else:
                logger.error(f"Telegram送信失敗: {resp.status_code} {resp.text[:200]}")
                return False
        except Exception as e:
            logger.error(f"Telegram送信エラー: {e}")
            return False

    def send_long(self, text: str, parse_mode: str = "HTML") -> bool:
        """4096文字超のメッセージを分割して送信"""
        if len(text) <= 4096:
            return self.send(text, parse_mode)

        chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
        results = [self.send(chunk, parse_mode) for chunk in chunks]
        return all(results)

    def test_connection(self) -> bool:
        """接続テスト用にシンプルなメッセージを送信"""
        return self.send("✅ 株式推奨Bot接続テスト成功")


def format_report(candidates, macro_snap: dict, season_label: str = "") -> str:
    """
    推奨レポートをTelegram用HTMLフォーマットに変換

    Args:
        candidates  : Candidateリスト
        macro_snap  : マクロスナップショット
        season_label: 例 "【決算シーズン通知】"
    """
    from datetime import date
    lines = []

    header = f"📊 <b>株式推奨レポート</b>  {date.today()}"
    if season_label:
        header = f"{season_label}\n{header}"
    lines.append(header)
    lines.append("")

    # マクロ環境
    lines.append("🌐 <b>マクロ環境</b>")
    lines.append(macro_snap.get("macro_summary", "N/A"))
    lines.append("")

    if not candidates:
        lines.append("⚠️ <b>推奨銘柄: なし</b>")
        lines.append("現在の市場環境では推奨できる銘柄が見当たりません。")
        return "\n".join(lines)

    lines.append(f"📋 <b>推奨銘柄 {len(candidates)}銘柄</b>")
    lines.append("─" * 28)

    for i, c in enumerate(candidates, 1):
        val  = c.valuation
        cur  = c.currency
        sym  = "¥" if cur == "JPY" else "$"

        def fp(v):
            if v is None: return "N/A"
            return f"{sym}{v:,.0f}" if cur == "JPY" else f"{sym}{v:.2f}"

        icon = "🟢" if "強く" in c.recommendation else "🔵"
        upside = f"+{val.upside_pct:.1f}%" if val.upside_pct and val.upside_pct >= 0 else (f"{val.upside_pct:.1f}%" if val.upside_pct else "N/A")

        lines.append(f"{icon} <b>{i}. {c.name}（{c.symbol}）</b>")
        lines.append(f"   {c.recommendation}  確度:{c.confidence}  スコア:{c.composite_score:.1f}/10")
        lines.append(f"   💴 現在株価: {fp(val.current_price)}")
        lines.append(f"   📐 理論株価(中央): {fp(val.fair_value_mid)}  上昇余地: {upside}")
        target_str = fp(val.take_profit)
        if c.months_to_target:
            target_str += f"（到達見込み: {c.months_to_target}）"
        lines.append(f"   ✂️ 損切: {fp(val.stop_loss)}　🎯 利確: {target_str}")

        if val.analyst_target and val.current_price and val.analyst_target > val.current_price:
            a_up = (val.analyst_target - val.current_price) / val.current_price * 100
            lines.append(f"   👥 アナリスト目標: {fp(val.analyst_target)} (+{a_up:.1f}%)")

        # インサイダー取引（中立は表示省略）
        ins_sent = getattr(c, "insider_sentiment", None)
        if ins_sent and ins_sent != "中立":
            ins_icon = "🟢" if ins_sent == "買い越し" else "🔴"
            lines.append(f"   {ins_icon} インサイダー: {ins_sent}")

        # EPSサプライズ beat率
        eps_rate = getattr(c, "eps_beat_rate", None)
        eps_q    = getattr(c, "eps_total_quarters", 0)
        eps_avg  = getattr(c, "eps_avg_surprise_pct", None)
        if eps_rate is not None and eps_q >= 2:
            avg_str = f"　平均 {eps_avg:+.1f}%" if eps_avg is not None else ""
            lines.append(
                f"   📈 EPS beat率: {eps_rate:.0%}（{eps_q}四半期{avg_str}）"
            )

        if c.bull_case:
            lines.append(f"   🟢 根拠: {c.bull_case[0]}")
        if c.key_risks:
            lines.append(f"   ⚠️ リスク: {c.key_risks[0]}")

        lines.append("")

    lines.append("─" * 28)
    lines.append("<i>⚠️ 本レポートは情報提供のみ。投資は自己責任で。</i>")
    return "\n".join(lines)
