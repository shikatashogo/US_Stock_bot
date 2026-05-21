#!/usr/bin/env python3
"""
バックテスト専用スクリプト
詳細な結果をCSVファイルに保存する
"""
import sys
import csv
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv("config/.env")

from src.orchestrator.scheduler import load_settings
from src.backtest.engine import BacktestEngine
from src.backtest.metrics import BacktestMetrics
from rich.console import Console

console = Console()


def main():
    settings = load_settings()
    engine = BacktestEngine(settings)
    metrics = BacktestMetrics()

    # テスト対象銘柄（設定のプライマリユニバースから取得）
    symbols = [s for s in settings["universe"]["primary"] if s not in ("SPY", "QQQ")]

    console.print(f"\n[bold]🔬 バックテスト実行[/bold]")
    console.print(f"期間: {settings['backtest']['start_date']} 〜 {settings['backtest']['end_date']}")
    console.print(f"初期資金: ${settings['backtest']['initial_capital']:,}")
    console.print(f"対象銘柄: {len(symbols)}銘柄\n")

    all_results = []
    all_trades = []

    for symbol in symbols:
        console.print(f"  {symbol} をバックテスト中...", end="")
        result = engine.run_swing_backtest(symbol)
        all_results.append(result)

        if result.get("trades"):
            all_trades.extend(result["trades"])

        grade = result.get("testa_evaluation", {}).get("grade", "N/A")
        pnl = result.get("total_pnl", 0)
        console.print(f" グレード:{grade} / 損益:${pnl:+.2f}")

    # 詳細レポートを表示
    console.print("\n" + "=" * 60)
    for result in all_results:
        report = metrics.format_report(result)
        console.print(report)
        console.print("")

    # 結果をCSVに保存
    if all_trades:
        output_dir = Path("reports")
        output_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = output_dir / f"backtest_{timestamp}.csv"

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_trades[0].keys())
            writer.writeheader()
            writer.writerows(all_trades)

        console.print(f"[green]✅ 取引詳細をCSV保存: {csv_path}[/green]")


if __name__ == "__main__":
    main()
