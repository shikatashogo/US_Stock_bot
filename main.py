#!/usr/bin/env python3
"""
US Stock Trading Bot - メインエントリポイント
使い方:
  python main.py              → ペーパートレードモードで起動
  python main.py --live       → 本番取引モードで起動（要確認）
  python main.py --backtest   → バックテストを実行
  python main.py --status     → 現在の取引状況を表示
"""
import argparse
import os
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from loguru import logger
from rich.console import Console
from rich.table import Table
from rich import print as rprint

load_dotenv("config/.env")

from src.orchestrator.scheduler import TradingBot, load_settings
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import BacktestMetrics
from src.execution.trade_logger import TradeLogger
from src.orchestrator.market_hours import MarketHoursManager


console = Console()


def setup_logging(settings: dict):
    """ロギングを設定する"""
    log_config = settings.get("logging", {})
    log_level = log_config.get("level", "INFO")
    log_file = log_config.get("file", "logs/bot.log")

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(sys.stderr, level=log_level, colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
    logger.add(log_file, level="DEBUG", rotation="1 day", retention="30 days",
               encoding="utf-8")


def run_bot(paper_trading: bool = True):
    """Botを起動する"""
    settings = load_settings()
    setup_logging(settings)

    if not paper_trading:
        console.print("\n[bold red]⚠️  警告: 本番取引モードで起動します！[/bold red]")
        console.print("実際の資金での取引が実行されます。")
        confirm = input("本当に続けますか？ (yes と入力して確認): ")
        if confirm.lower() not in ("yes", "y"):
            console.print("起動をキャンセルしました。")
            return

    bot = TradingBot(settings, paper_trading=paper_trading)
    bot.start()


def run_backtest(symbols: list = None):
    """バックテストを実行して結果を表示する"""
    settings = load_settings()
    setup_logging(settings)

    if not symbols:
        primary = settings.get("universe", {}).get("primary", [])
        symbols = [s for s in primary if s not in ("SPY", "QQQ")][:4]
        if not symbols:
            symbols = ["AAPL", "MSFT", "NVDA", "AMZN"]

    console.print(f"\n[bold cyan]📊 バックテスト実行中...[/bold cyan]")
    console.print(f"対象銘柄: {', '.join(symbols)}")
    console.print(f"期間: {settings['backtest']['start_date']} 〜 {settings['backtest']['end_date']}")
    console.print("")

    engine = BacktestEngine(settings)
    metrics = BacktestMetrics()

    for symbol in symbols:
        result = engine.run_swing_backtest(symbol)
        report = metrics.format_report(result)
        console.print(report)
        console.print("")


def show_status():
    """現在の取引状況を表示する"""
    trade_logger = TradeLogger()
    market_hours = MarketHoursManager()

    console.print("\n[bold cyan]📊 取引Bot ステータス[/bold cyan]")
    console.print(f"市場状態: {market_hours.get_market_status_str()}")
    console.print("")

    # オープンポジション
    open_trades = trade_logger.get_open_trades()
    if open_trades:
        table = Table(title="📂 オープンポジション")
        table.add_column("銘柄", style="cyan")
        table.add_column("戦略")
        table.add_column("株数")
        table.add_column("エントリー")
        table.add_column("損切り")
        table.add_column("利確目標")
        table.add_column("エントリー時刻")

        for trade in open_trades:
            table.add_row(
                trade["symbol"],
                trade.get("strategy", "N/A"),
                str(trade["shares"]),
                f"${trade['entry_price']:.2f}",
                f"${trade['stop_loss']:.2f}",
                f"${trade['take_profit']:.2f}",
                trade.get("entry_time", "N/A")[:16],
            )
        console.print(table)
    else:
        console.print("[yellow]オープンポジションなし[/yellow]")

    console.print("")

    # 30日パフォーマンス
    stats = trade_logger.get_performance_stats(days=30)
    if stats.get("total_trades", 0) > 0:
        table2 = Table(title="📈 30日間パフォーマンス")
        table2.add_column("指標")
        table2.add_column("値", justify="right")

        table2.add_row("総取引数", str(stats["total_trades"]))
        table2.add_row("勝率", f"{stats['win_rate']}%")
        table2.add_row("総損益", f"${stats['total_pnl']:+.2f}")
        table2.add_row("平均利益", f"${stats['avg_win']:.2f}")
        table2.add_row("平均損失", f"${stats['avg_loss']:.2f}")
        table2.add_row("プロフィットファクター", str(stats["profit_factor"]))

        console.print(table2)
    else:
        console.print("[yellow]30日間の取引データなし[/yellow]")

    # 直近取引
    recent = trade_logger.get_recent_trades(limit=5)
    if recent:
        console.print("")
        table3 = Table(title="🕐 直近の取引")
        table3.add_column("銘柄", style="cyan")
        table3.add_column("損益", justify="right")
        table3.add_column("損益率", justify="right")
        table3.add_column("保有時間")
        table3.add_column("理由")

        for trade in recent:
            pnl = trade.get("pnl", 0)
            pnl_color = "green" if pnl >= 0 else "red"
            table3.add_row(
                trade["symbol"],
                f"[{pnl_color}]${pnl:+.2f}[/{pnl_color}]",
                f"[{pnl_color}]{trade.get('pnl_pct', 0):+.1f}%[/{pnl_color}]",
                f"{int(trade.get('hold_minutes', 0) // 60)}h{int(trade.get('hold_minutes', 0) % 60)}m",
                str(trade.get("notes", ""))[:30],
            )
        console.print(table3)


def main():
    parser = argparse.ArgumentParser(
        description="US Stock Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python main.py                          # ペーパートレードで起動
  python main.py --live                   # 本番取引で起動
  python main.py --backtest               # バックテスト実行（デフォルト銘柄）
  python main.py --backtest AAPL NVDA     # 指定銘柄でバックテスト
  python main.py --status                 # 取引状況を表示
        """
    )
    parser.add_argument("--live", action="store_true", help="本番取引モードで起動")
    parser.add_argument("--backtest", nargs="*", metavar="SYMBOL", help="バックテストを実行")
    parser.add_argument("--status", action="store_true", help="現在の取引状況を表示")

    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.backtest is not None:
        symbols = args.backtest if args.backtest else None
        run_backtest(symbols)
    else:
        paper_trading = not args.live
        run_bot(paper_trading=paper_trading)


if __name__ == "__main__":
    main()
