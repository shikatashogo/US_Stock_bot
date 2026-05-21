"""
東証ORBバックテスト 実行スクリプト
====================================
使い方:
  python run_tse_backtest.py
  python run_tse_backtest.py --rr 2.5
  python run_tse_backtest.py --symbols 8306 6758 7203 --rr 2.0
  python run_tse_backtest.py --sweep   # パラメータスイープ（RR比の感度分析）
"""
import argparse
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from loguru import logger
from src.backtest.tse_orb_backtest import BacktestConfig, TSEORBBacktester

logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")


def run_single(args) -> None:
    """単一パラメータでバックテスト実行"""
    symbols = args.symbols if args.symbols else None  # Noneでデフォルト10銘柄

    config = BacktestConfig(
        capital=args.capital,
        leverage=args.leverage,
        rr_ratio=args.rr,
        risk_per_trade_pct=args.risk_pct / 100,
        symbols=symbols or BacktestConfig.__dataclass_fields__["symbols"].default_factory(),
        min_volume_ratio=args.min_vol_ratio,
    )

    bt = TSEORBBacktester(config)
    results = bt.run()
    bt.print_summary(results)

    if results["summary"].get("total_trades", 0) > 0:
        csv_path = ROOT / "data" / "tse_orb_trades.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        bt.export_trades_csv(results, str(csv_path))
        print(f"\n取引履歴CSV: {csv_path}")

    # 100取引に届かない場合の警告
    total = results["summary"].get("total_trades", 0)
    if total < 30:
        print(f"\n⚠️  取引数が{total}回と少なく、統計的信頼性が低いです。")
        print("   yfinanceの5分足データは約60日分のみ取得可能です。")
        print("   銘柄数を増やすか、J-Quants APIの導入を検討してください。")
    elif total < 100:
        print(f"\n📊 取引数{total}回。100回以上が推奨ですが参考値として活用できます。")


def run_sweep(args) -> None:
    """RR比・レンジ幅フィルターのパラメータスイープ"""
    print("\n=== パラメータスイープ（RR比感度分析）===")
    print(f"{'RR比':>6} | {'取引数':>6} | {'勝率':>8} | {'純損益':>12} | {'PF':>6} | {'DD':>10}")
    print("-" * 65)

    for rr in [1.5, 2.0, 2.5, 3.0]:
        config = BacktestConfig(
            capital=args.capital,
            leverage=args.leverage,
            rr_ratio=rr,
            risk_per_trade_pct=args.risk_pct / 100,
        )
        bt = TSEORBBacktester(config)
        results = bt.run()
        s = results["summary"]

        if s.get("total_trades", 0) == 0:
            print(f"{rr:>6.1f} | {'0':>6} | {'N/A':>8} | {'N/A':>12} | {'N/A':>6} | {'N/A':>10}")
            continue

        print(
            f"{rr:>6.1f} | {s['total_trades']:>6} | {s['win_rate']:>7.1f}% | "
            f"¥{s['total_pnl']:>10,.0f} | {s['profit_factor']:>6.2f} | "
            f"-¥{s['max_drawdown']:>8,.0f}"
        )


def main():
    parser = argparse.ArgumentParser(description="TSE ORBバックテスト")
    parser.add_argument("--rr", type=float, default=2.0, help="リスクリワード比（デフォルト: 2.0）")
    parser.add_argument("--capital", type=float, default=500_000, help="初期資金 円（デフォルト: 500000）")
    parser.add_argument("--leverage", type=float, default=2.0, help="信用取引レバレッジ（デフォルト: 2.0）")
    parser.add_argument("--risk-pct", type=float, default=0.5, help="1取引リスク%（デフォルト: 0.5）")
    parser.add_argument("--min-vol-ratio", type=float, default=1.3, help="出来高フィルター倍率（デフォルト: 1.3）")
    parser.add_argument("--symbols", nargs="+", help="銘柄コード（スペース区切り）")
    parser.add_argument("--sweep", action="store_true", help="パラメータスイープモード")

    args = parser.parse_args()

    if args.sweep:
        run_sweep(args)
    else:
        run_single(args)


if __name__ == "__main__":
    main()
