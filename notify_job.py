"""
自動通知ジョブ
==============
決算シーズン中に自動実行し、推奨銘柄をTelegramに通知する。

実行方法:
  python notify_job.py           # 決算シーズン判定して通知
  python notify_job.py --force   # シーズン外でも強制実行
  python notify_job.py --test    # Telegram接続テストのみ
  python notify_job.py --dry-run # 通知せず内容だけ確認

cronへの登録（Mac）:
  crontab -e で以下を追加
  0 8 * * 1 cd /path/to/US_Stock_bot && venv/bin/python notify_job.py

GitHub Actions:
  .github/workflows/notify.yml を参照

決算シーズン定義:
  日本株: 2月・5月・8月・11月（主要企業の決算発表が集中）
  米国株: 1月・4月・7月・10月（四半期決算シーズン）
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from loguru import logger

from config.universe import get_all_symbols, get_japan_symbols, get_us_symbols
from src.analysis.pipeline import run_pipeline
from src.notify.telegram_notifier import TelegramNotifier, format_report

logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")

# ─── 決算シーズン定義 ────────────────────────────────────────────

# 日本株: 3月決算企業が多く、1Q=8月、2Q=11月、3Q=2月、本決算=5月
# 米国株: 1月・4月・7月・10月が決算シーズン
JAPAN_EARNINGS_MONTHS  = {2, 5, 8, 11}
US_EARNINGS_MONTHS     = {1, 4, 7, 10}
ALL_EARNINGS_MONTHS    = JAPAN_EARNINGS_MONTHS | US_EARNINGS_MONTHS  # {1,2,4,5,7,8,10,11}

# 何日以降に通知するか（月初から数えた日数）
NOTIFY_AFTER_DAY = 1   # 月初1日以降は毎週月曜に通知


def is_earnings_season(target_date: date | None = None) -> tuple[bool, str]:
    """
    決算シーズンかどうかを判定する

    Returns:
        (is_season: bool, label: str)
    """
    d = target_date or date.today()
    month = d.month

    if month in JAPAN_EARNINGS_MONTHS and month in US_EARNINGS_MONTHS:
        return True, "【日米 決算シーズン通知】"
    elif month in JAPAN_EARNINGS_MONTHS:
        return True, "【🇯🇵 日本株 決算シーズン通知】"
    elif month in US_EARNINGS_MONTHS:
        return True, "【🇺🇸 米国株 決算シーズン通知】"
    else:
        return False, ""


def next_earnings_season() -> str:
    """次の決算シーズン開始月を返す"""
    today = date.today()
    for delta in range(1, 13):
        m = (today.month - 1 + delta) % 12 + 1
        if m in ALL_EARNINGS_MONTHS:
            return f"{m}月"
    return "不明"


# ─── 分析パイプライン ────────────────────────────────────────────
# 実装は src/analysis/pipeline.py の run_pipeline に一元化済み


# ─── メイン ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="株式推奨Bot 自動通知ジョブ")
    parser.add_argument("--force",   action="store_true", help="決算シーズン外でも強制実行")
    parser.add_argument("--test",    action="store_true", help="Telegram接続テストのみ")
    parser.add_argument("--dry-run", action="store_true", help="通知せず内容だけ表示")
    parser.add_argument("--jp",      action="store_true", help="日本株のみ分析")
    parser.add_argument("--us",      action="store_true", help="米国株のみ分析")
    parser.add_argument("--top",     type=int, default=8,  help="通知する上位N銘柄")
    args = parser.parse_args()

    notifier = TelegramNotifier()

    # ── 接続テスト ───────────────────────────────────────────────
    if args.test:
        if not notifier.is_configured():
            print(
                "\n❌ Telegram未設定。以下を .env に追加してください:\n"
                "  TELEGRAM_BOT_TOKEN=xxxx:yyy\n"
                "  TELEGRAM_CHAT_ID=123456789\n"
                "\n設定方法は下記を参照:\n"
                "  1. Telegramで @BotFather に /newbot と送信\n"
                "  2. 指示に従いBot作成 → トークン取得\n"
                "  3. 自分のBotにメッセージを送る\n"
                "  4. https://api.telegram.org/bot<TOKEN>/getUpdates でchat_id確認\n"
            )
        else:
            success = notifier.test_connection()
            print("✅ 接続成功" if success else "❌ 接続失敗（トークンとchat_idを確認してください）")
        return

    # ── 決算シーズン判定 ─────────────────────────────────────────
    is_season, season_label = is_earnings_season()

    if not is_season and not args.force:
        next_s = next_earnings_season()
        logger.info(
            f"現在は決算シーズン外（{date.today().month}月）。"
            f"次の決算シーズン: {next_s}。"
            f"強制実行するには --force を付けてください。"
        )
        return

    if args.force and not is_season:
        season_label = "【手動実行】"
        logger.info("--force: 決算シーズン外だが強制実行します")

    # ── 分析実行 ─────────────────────────────────────────────────
    if args.jp:
        symbols = get_japan_symbols()
    elif args.us:
        symbols = get_us_symbols()
    else:
        symbols = get_all_symbols()

    candidates, macro_snap = run_pipeline(symbols, use_cache=True, top_n=args.top)

    # ── 通知テキスト生成 ─────────────────────────────────────────
    message = format_report(candidates, macro_snap, season_label)

    if args.dry_run:
        print("\n" + "=" * 60)
        print("【DRY RUN】実際には送信しません。送信予定の内容:")
        print("=" * 60)
        # HTMLタグを簡易除去して表示
        import re
        plain = re.sub(r"<[^>]+>", "", message)
        print(plain)
        print("=" * 60)
        return

    # ── Telegram送信 ────────────────────────────────────────────
    if not notifier.is_configured():
        logger.warning(
            "Telegram未設定のため通知をスキップ。\n"
            ".env に TELEGRAM_BOT_TOKEN と TELEGRAM_CHAT_ID を設定してください。"
        )
        # 未設定でもコンソールには表示する
        import re
        print(re.sub(r"<[^>]+>", "", message))
        return

    success = notifier.send_long(message)
    if success:
        logger.info("通知完了")
    else:
        logger.error("通知失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
