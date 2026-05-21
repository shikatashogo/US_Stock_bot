"""
日次スクリーニング 実行スクリプト
====================================
毎日市場クローズ後（16:00〜）に実行して翌日の候補銘柄を選定する

使い方:
  python run_screener.py             # 通常実行（結果を表示・保存）
  python run_screener.py --top 5     # 上位5銘柄のみ
  python run_screener.py --no-save   # ファイル保存なし（テスト用）
  python run_screener.py --show-all  # 全銘柄スコアを表示

cron 登録例（毎日16:30に実行）:
  30 16 * * 1-5 cd /path/to/bot && venv/bin/python run_screener.py
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from loguru import logger
from src.screening.daily_screener import DailyScreener

logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")


def main():
    parser = argparse.ArgumentParser(description="日次スクリーニング")
    parser.add_argument("--top", type=int, default=8, help="選出する上位銘柄数（デフォルト: 8）")
    parser.add_argument("--min-score", type=float, default=3.0, help="最低スコア閾値（デフォルト: 3.0）")
    parser.add_argument("--no-save", action="store_true", help="結果をファイルに保存しない")
    parser.add_argument("--show-all", action="store_true", help="全銘柄のスコアを表示")
    parser.add_argument("--show-latest", action="store_true", help="保存済みの最新結果を表示")
    args = parser.parse_args()

    screener = DailyScreener(
        capital=500_000,
        top_n=args.top,
        min_score=args.min_score,
    )

    # 保存済み結果の表示モード
    if args.show_latest:
        result = screener.load_latest()
        if result:
            from dataclasses import asdict
            from src.screening.daily_screener import ScreeningResult
            # dictから表示
            print(f"\n保存済みスクリーニング結果: {result['target_date']} 取引用")
            ctx = result["market_context"]
            print(f"市場環境: {ctx.get('market_regime', '?').upper()}")
            print(f"日経 {ctx.get('nikkei_change_pct', 0):+.2f}% / ドル円 {ctx.get('usdjpy', 0):.1f}")
            print("\n選出銘柄:")
            for i, c in enumerate(result["candidates"], 1):
                print(f"  {i}. [{c['code']}] {c['name']} スコア{c['score']:.1f} | {', '.join(c['signals'])}")
        return

    # スクリーニング実行
    result = screener.run(save=not args.no_save)
    screener.print_result(result)

    # 全銘柄スコア表示
    if args.show_all and result.all_scores:
        print("\n全銘柄スコア一覧:")
        print(f"  {'コード':>6} {'銘柄':>12} {'スコア':>6} {'Vol比':>6} {'NR7':>4}")
        print("  " + "-" * 40)
        for s in result.all_scores:
            nr7 = "✓" if s["is_nr7"] else "-"
            print(f"  {s['code']:>6} {s['name']:>12} {s['score']:>6.1f} {s['vol_ratio']:>6.2f}x {nr7:>4}")


if __name__ == "__main__":
    main()
