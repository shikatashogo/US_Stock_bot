"""
GF戦略（Gap × First-5min Momentum）vs ORB 比較バックテスト
=============================================================
実行方法:
  python run_gf_backtest.py              # ORB vs GF比較（デフォルト設定）
  python run_gf_backtest.py --sweep      # ギャップ閾値 × トレイル係数のパラメータスイープ
  python run_gf_backtest.py --gap 0.003  # ギャップ閾値を0.3%に変更
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from loguru import logger
from src.backtest.gf_backtest import GFBacktester, GFConfig
from src.backtest.tse_data_fetcher import TSEDataFetcher
from src.backtest.tse_orb_backtest import BacktestConfig, TSEORBBacktester

logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")

SYMBOLS = [
    "8306", "6758", "7203", "9432", "9984",
    "6954", "4063", "6503", "9433", "8316",
]


# ─── 比較テーブル表示 ──────────────────────────────────────────────

def print_comparison(orb_results: dict, gf_results: dict) -> None:
    """ORB vs GF の並列比較テーブル"""
    o = orb_results.get("summary", {})
    g = gf_results.get("summary", {})

    print("\n" + "=" * 65)
    print("  ORB戦略 vs GF戦略（Gap × First-5min Momentum）比較")
    print("=" * 65)
    print(f"  {'指標':22}  {'ORB（30分待機+固定RR）':>18}  {'GF（5分+トレイル）':>16}")
    print("  " + "-" * 63)

    rows = [
        ("取引数",          "total_trades",      "{:.0f}",    "{:.0f}"),
        ("勝率",            "win_rate",           "{:.1f}%",   "{:.1f}%"),
        ("純損益",          "total_pnl",          "¥{:,.0f}",  "¥{:,.0f}"),
        ("PF（損益比）",    "profit_factor",      "{:.2f}",    "{:.2f}"),
        ("期待値/取引",     "expectancy",         "¥{:,.0f}",  "¥{:,.0f}"),
        ("平均勝ち",        "avg_win",            "¥{:,.0f}",  "¥{:,.0f}"),
        ("平均負け",        "avg_loss",           "¥{:,.0f}",  "¥{:,.0f}"),
        ("最大DD",          "max_drawdown",       "¥{:,.0f}",  "¥{:,.0f}"),
        ("最大DD%",         "max_drawdown_pct",   "{:.1f}%",   "{:.1f}%"),
        ("平均保有(分)",    "avg_hold_minutes",   "{:.0f}分",  "{:.0f}分"),
    ]

    for label, key, ofmt, gfmt in rows:
        ov = o.get(key)
        gv = g.get(key)
        os = ofmt.format(ov) if ov is not None else "N/A"
        gs = gfmt.format(gv) if gv is not None else "N/A"
        # 改善した方をハイライト（★）
        star_o, star_g = "", ""
        if ov is not None and gv is not None and key not in ("total_trades", "avg_hold_minutes"):
            if key in ("max_drawdown", "max_drawdown_pct", "avg_loss"):
                if gv < ov: star_g = " ★"
                elif ov < gv: star_o = " ★"
            else:
                if gv > ov: star_g = " ★"
                elif ov > gv: star_o = " ★"
        print(f"  {label:22}  {os+star_o:>20}  {gs+star_g:>18}")

    print("  " + "-" * 63)

    # エグジット理由比較
    print("\n  エグジット理由内訳:")
    all_reasons = set(list(o.get("exit_reasons", {}).keys()) + list(g.get("exit_reasons", {}).keys()))
    ot = o.get("total_trades", 1) or 1
    gt = g.get("total_trades", 1) or 1
    print(f"  {'理由':16}  {'ORB':>12}  {'GF':>12}")
    for reason in sorted(all_reasons):
        oc = o.get("exit_reasons", {}).get(reason, 0)
        gc = g.get("exit_reasons", {}).get(reason, 0)
        print(f"  {reason:16}  {oc:>4}回({oc/ot*100:>4.0f}%)  {gc:>4}回({gc/gt*100:>4.0f}%)")

    # 月次損益比較
    orb_m = orb_results.get("monthly", {})
    gf_m  = gf_results.get("monthly", {})
    all_months = sorted(set(list(orb_m.keys()) + list(gf_m.keys())))
    if all_months:
        print(f"\n  月次損益比較:")
        print(f"  {'月':>8}  {'ORB':>12}  {'GF':>12}  {'差分(GF-ORB)':>14}")
        print("  " + "-" * 52)
        for m in all_months:
            ob = orb_m.get(m, 0)
            gb = gf_m.get(m, 0)
            diff = gb - ob
            sign = "+" if diff >= 0 else ""
            print(f"  {m:>8}  {ob:>+12,.0f}  {gb:>+12,.0f}  {sign}{diff:>12,.0f}")

    print("\n" + "=" * 65)

    # GF追加指標
    if g:
        print(f"\n  GF戦略 追加指標:")
        print(f"    平均ギャップ率  : {g.get('avg_gap_pct', 0):.2f}%")
        print(f"    平均MFE（最大含み益）: {g.get('avg_mfe_pct', 0):.2f}%")
        print(f"    Long/Short内訳  : {g.get('long_count', 0)}L / {g.get('short_count', 0)}S")


# ─── パラメータスイープ ────────────────────────────────────────────

def run_sweep(intraday_data, daily_data) -> None:
    """ギャップ閾値 × トレイル係数のグリッドサーチ"""
    gap_thresholds   = [0.003, 0.005, 0.007, 0.010]
    trail_factors    = [0.3, 0.5, 0.7, 1.0]

    print("\n=== パラメータスイープ（ギャップ閾値 × トレイル係数）===")
    print(f"{'gap%':>6} | {'trail':>6} | {'取引数':>6} | {'勝率':>7} | {'純損益':>10} | {'PF':>5} | {'DD%':>6}")
    print("-" * 62)

    best_pf     = 0.0
    best_params = {}

    for gap_pct in gap_thresholds:
        for trail in trail_factors:
            cfg = GFConfig(
                min_gap_pct     = gap_pct,
                trail_atr_factor = trail,
                symbols          = SYMBOLS,
            )
            bt = GFBacktester(cfg)
            results = bt.run(intraday_data=intraday_data, daily_data=daily_data)
            s = results.get("summary", {})

            if not s or s.get("total_trades", 0) == 0:
                print(f"{gap_pct*100:>5.1f}% | {trail:>6.1f} | {'0':>6} | {'N/A':>7} | {'N/A':>10} | {'N/A':>5} | {'N/A':>6}")
                continue

            pf = s["profit_factor"]
            if pf != float("inf") and pf > best_pf:
                best_pf     = pf
                best_params = {"gap_pct": gap_pct, "trail": trail, "results": s}

            pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
            print(
                f"{gap_pct*100:>5.1f}% | {trail:>6.1f} | "
                f"{s['total_trades']:>6} | {s['win_rate']:>6.1f}% | "
                f"¥{s['total_pnl']:>8,.0f} | {pf_str:>5} | "
                f"{s['max_drawdown_pct']:>5.1f}%"
            )

    if best_params:
        print(f"\n  最良パラメータ: gap={best_params['gap_pct']*100:.1f}% "
              f"trail={best_params['trail']:.1f} "
              f"(PF={best_pf:.2f})")


# ─── メイン ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GF戦略 vs ORB 比較バックテスト")
    parser.add_argument("--gap",   type=float, default=0.005, help="ギャップ閾値（デフォルト0.5%）")
    parser.add_argument("--trail", type=float, default=0.5,   help="トレイルATR係数（デフォルト0.5）")
    parser.add_argument("--rr",    type=float, default=2.0,   help="ORBのRR比（デフォルト2.0）")
    parser.add_argument("--capital", type=float, default=500_000)
    parser.add_argument("--sweep", action="store_true", help="パラメータスイープモード")
    args = parser.parse_args()

    # データを事前に取得（ORB・GF両方で共有してキャッシュを活用）
    logger.info("データ取得中（ORB・GF共用）...")
    fetcher        = TSEDataFetcher()
    intraday_data  = fetcher.fetch_universe(SYMBOLS, interval="5m")
    daily_data     = fetcher.fetch_universe(SYMBOLS, interval="1d")

    if args.sweep:
        run_sweep(intraday_data, daily_data)
        return

    # ── ORBバックテスト ────────────────────────────────────────
    logger.info("=== ORB バックテスト ===")
    orb_cfg     = BacktestConfig(capital=args.capital, rr_ratio=args.rr, symbols=SYMBOLS)
    orb_bt      = TSEORBBacktester(orb_cfg)
    orb_results = orb_bt.run()

    # ── GFバックテスト ────────────────────────────────────────
    logger.info("=== GF バックテスト ===")
    gf_cfg     = GFConfig(
        capital          = args.capital,
        min_gap_pct      = args.gap,
        trail_atr_factor = args.trail,
        symbols          = SYMBOLS,
    )
    gf_bt      = GFBacktester(gf_cfg)
    gf_results = gf_bt.run(intraday_data=intraday_data, daily_data=daily_data)

    # ── 比較表示 ──────────────────────────────────────────────
    print_comparison(orb_results, gf_results)

    # 詳細サマリー
    orb_bt.print_summary(orb_results)
    gf_bt.print_summary(gf_results)


if __name__ == "__main__":
    main()
