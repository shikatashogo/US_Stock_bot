"""
ORB + ギャップ方向フィルター 比較バックテスト
=============================================
「寄り付きギャップ方向と一致するORBシグナルのみエントリー」戦略を検証する

比較パターン:
  ① ORB 両方向（ベースライン）
  ② ORB + ギャップフィルター 閾値0.1%（中立ギャップは両方向OK）
  ③ ORB + ギャップフィルター 閾値0.3%
  ④ ORB + ギャップフィルター 閾値0.5%

実行方法:
  python run_gap_filtered_orb.py
  python run_gap_filtered_orb.py --rr 2.5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from loguru import logger
from src.backtest.tse_data_fetcher import TSEDataFetcher
from src.backtest.tse_orb_backtest import BacktestConfig, TSEORBBacktester

logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")

SYMBOLS = [
    "8306", "6758", "7203", "9432", "9984",
    "6954", "4063", "6503", "9433", "8316",
]


def run_pattern(
    label: str,
    gap_filter: bool,
    gap_threshold: float,
    rr: float,
    capital: float,
) -> dict:
    cfg = BacktestConfig(
        capital              = capital,
        rr_ratio             = rr,
        symbols              = SYMBOLS,
        gap_direction_filter = gap_filter,
        gap_filter_threshold = gap_threshold,
    )
    bt      = TSEORBBacktester(cfg)
    results = bt.run()   # TSEDataFetcher のpickleキャッシュを活用
    s       = results.get("summary", {})
    total   = s.get("total_trades", 0)
    logger.info(
        f"[{label}] 取引={total} 勝率={s.get('win_rate',0):.1f}% "
        f"PF={s.get('profit_factor',0):.2f} 純損益=¥{s.get('total_pnl',0):,.0f}"
    )
    results["label"] = label
    return results


def print_comparison(all_results: list[dict]) -> None:
    """全パターンの横並び比較テーブル"""
    print("\n" + "=" * 80)
    print("  ORB ギャップ方向フィルター 比較結果")
    print("=" * 80)

    # ヘッダー
    print(f"  {'指標':22}", end="")
    for r in all_results:
        print(f"  {r['label']:>16}", end="")
    print()
    print("  " + "-" * 78)

    metrics = [
        ("取引数",         "total_trades",      "{:.0f}"),
        ("勝率",           "win_rate",           "{:.1f}%"),
        ("純損益",         "total_pnl",          "¥{:,.0f}"),
        ("PF（損益比）",   "profit_factor",      "{:.2f}"),
        ("期待値/取引",    "expectancy",         "¥{:,.0f}"),
        ("平均勝ち",       "avg_win",            "¥{:,.0f}"),
        ("平均負け",       "avg_loss",           "¥{:,.0f}"),
        ("最大DD",         "max_drawdown",       "¥{:,.0f}"),
        ("最大DD%",        "max_drawdown_pct",   "{:.1f}%"),
        ("平均保有(分)",   "avg_hold_minutes",   "{:.0f}分"),
    ]

    for disp_label, key, fmt in metrics:
        print(f"  {disp_label:22}", end="")
        vals = [r.get("summary", {}).get(key) for r in all_results]
        for i, (r, v) in enumerate(zip(all_results, vals)):
            if v is None:
                print(f"  {'N/A':>16}", end="")
                continue
            if isinstance(v, float) and v == float("inf"):
                print(f"  {'∞':>16}", end="")
                continue
            txt = fmt.format(v)
            # 最良値に★マーク
            star = ""
            if key in ("max_drawdown", "max_drawdown_pct", "avg_loss"):
                best = min(x for x in vals if x is not None)
                if v == best: star = "★"
            elif key != "total_trades":
                best = max(x for x in vals if x is not None)
                if v == best: star = "★"
            print(f"  {txt+star:>16}", end="")
        print()

    # エグジット理由（lunch_close率）
    print("\n  " + "-" * 78)
    print("  エグジット理由内訳（lunch_close / tp / sl / force）:")
    for r in all_results:
        s   = r.get("summary", {})
        er  = s.get("exit_reasons", {})
        tot = s.get("total_trades", 1) or 1
        lc  = er.get("lunch_close", 0)
        tp  = er.get("tp", 0)
        sl  = er.get("sl", 0)
        fc  = er.get("force_close", 0)
        print(
            f"  {r['label']:20}  "
            f"昼:{lc:>3}回({lc/tot*100:>4.0f}%)  "
            f"TP:{tp:>3}回({tp/tot*100:>4.0f}%)  "
            f"SL:{sl:>3}回({sl/tot*100:>4.0f}%)  "
            f"引:{fc:>3}回({fc/tot*100:>4.0f}%)"
        )

    # 月次損益
    all_months = sorted(set(
        m for r in all_results for m in r.get("monthly", {}).keys()
    ))
    if all_months:
        print(f"\n  月次損益:")
        header = f"  {'月':>8}"
        for r in all_results:
            header += f"  {r['label']:>16}"
        print(header)
        print("  " + "-" * 78)
        for m in all_months:
            row = f"  {m:>8}"
            for r in all_results:
                v = r.get("monthly", {}).get(m, 0)
                row += f"  {v:>+16,.0f}"
            print(row)

    print("\n" + "=" * 80)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rr",      type=float, default=2.0)
    parser.add_argument("--capital", type=float, default=500_000)
    args = parser.parse_args()

    # データはTSEDataFetcherのpickleキャッシュ経由で共有（再ダウンロード不要）
    patterns = [
        ("①ORB 両方向",      False, 0.000),
        ("②GapFilter 0.1%",  True,  0.001),
        ("③GapFilter 0.3%",  True,  0.003),
        ("④GapFilter 0.5%",  True,  0.005),
    ]

    all_results = []
    for label, gap_filter, gap_thr in patterns:
        logger.info(f"=== {label} ===")
        r = run_pattern(
            label        = label,
            gap_filter   = gap_filter,
            gap_threshold= gap_thr,
            rr           = args.rr,
            capital      = args.capital,
        )
        all_results.append(r)

    print_comparison(all_results)


if __name__ == "__main__":
    main()
