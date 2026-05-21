"""
スクリーニング統合バックテスト
================================
「固定10銘柄ORB」vs「スクリーナー選別ORB」の勝率・損益を定量比較する

3パターンを並べて検証:
  ① 固定10銘柄   : BacktestConfig のデフォルト銘柄を毎日全て対象
  ② スクリーナー3点↑: 29銘柄から前日スコア3.0以上・上位8銘柄を対象
  ③ スクリーナー5点↑: 同スコア5.0以上（高確信度フィルター）

実行方法:
  python run_screened_backtest.py
  python run_screened_backtest.py --rr 2.5
  python run_screened_backtest.py --no-cache   # キャッシュ無視して再取得
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import yfinance as yf
from loguru import logger

from src.backtest.tse_data_fetcher import TSEDataFetcher, calc_atr, calc_daily_limit
from src.backtest.tse_orb_backtest import BacktestConfig, ORBRange, Trade, TSEORBBacktester
from src.screening.daily_screener import DailyScreener
from src.screening.universe_manager import CANDIDATE_UNIVERSE

logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")

# ─── 定数 ────────────────────────────────────────────────────────────
# 固定ユニバース（BacktestConfigのデフォルトと同じ）
BASELINE_SYMBOLS: List[str] = [
    "8306", "6758", "7203", "9432", "9984",
    "6954", "4063", "6503", "9433", "8316",
]

# スクリーニング対象は全候補29銘柄
ALL_CANDIDATE_CODES: List[str] = list(CANDIDATE_UNIVERSE.keys())


# ─── データ取得 ───────────────────────────────────────────────────────

def fetch_index_history() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    日経225・ドル円・VIXの日足履歴を取得（バックテスト期間をカバーする6ヶ月分）
    yf.Ticker.history() を使うことでTZアウェアなインデックスが返る
    """
    logger.info("インデックス履歴取得中 (^N225, JPY=X, ^VIX)...")

    def _fetch(ticker_str: str, fallback_val: float) -> pd.DataFrame:
        try:
            t = yf.Ticker(ticker_str)
            hist = t.history(period="6mo", interval="1d")
            if hist.empty:
                raise ValueError(f"{ticker_str} データ空")
            return hist
        except Exception as e:
            logger.warning(f"{ticker_str} 取得失敗: {e}")
            return pd.DataFrame()

    nk_hist = _fetch("^N225", 38000.0)
    jpy_hist = _fetch("JPY=X", 150.0)
    vix_hist = _fetch("^VIX", 20.0)
    return nk_hist, jpy_hist, vix_hist


# ─── 日毎の選定銘柄マップ構築 ─────────────────────────────────────────

def build_day_symbols(
    all_daily_data: Dict[str, pd.DataFrame],
    trading_dates: List[pd.Timestamp],
    nk_hist: pd.DataFrame,
    jpy_hist: pd.DataFrame,
    vix_hist: pd.DataFrame,
    min_score: float,
    top_n: int = 8,
) -> Dict[str, List[str]]:
    """
    各取引日に対して、その日の「前日データのみ」を使いスクリーニングを実施する。
    ルックアヘッドバイアスなし（cutoff_date の前日データのみ参照）。

    Returns:
        {"2026-03-01": ["8306", "6758", ...], ...}
    """
    screener = DailyScreener(top_n=top_n, min_score=min_score)
    universe = CANDIDATE_UNIVERSE  # {code: name} の29銘柄辞書
    day_symbols: Dict[str, List[str]] = {}

    logger.info(
        f"スクリーニング計算中: {len(trading_dates)}日 × {len(universe)}銘柄 "
        f"(min_score={min_score})"
    )

    for trade_date in trading_dates:
        scores = screener.score_candidates_for_date(
            all_daily_data=all_daily_data,
            universe=universe,
            cutoff_date=trade_date,
            nikkei_hist=nk_hist,
            usdjpy_hist=jpy_hist,
            vix_hist=vix_hist,
        )
        selected = [s.code for s in scores if s.score >= min_score][:top_n]
        date_str = str(trade_date.date()) if hasattr(trade_date, "date") else str(trade_date)
        day_symbols[date_str] = selected

    selected_counts = [len(v) for v in day_symbols.values()]
    avg_sel = sum(selected_counts) / max(len(selected_counts), 1)
    logger.info(
        f"スクリーニング完了: 平均選出{avg_sel:.1f}銘柄/日"
        f"（最小{min(selected_counts)}〜最大{max(selected_counts)}）"
    )
    return day_symbols


# ─── 1日1銘柄シミュレーション（スクリーナー版）─────────────────────────

def run_backtest_with_symbols(
    label: str,
    intraday_data: Dict[str, pd.DataFrame],
    daily_data: Dict[str, pd.DataFrame],
    day_symbols: Dict[str, List[str]],
    config: BacktestConfig,
) -> dict:
    """
    day_symbols に従って、各取引日に選ばれた銘柄のみでORBバックテストを実行する。

    Args:
        label:         結果ラベル（ログ表示用）
        intraday_data: {code: 5分足DataFrame}
        daily_data:    {code: 日足DataFrame}
        day_symbols:   {"YYYY-MM-DD": [選定銘柄コード, ...]}
        config:        BacktestConfig
    """
    bt = TSEORBBacktester(config)
    bt.trades = []

    for symbol, df5 in intraday_data.items():
        if symbol not in daily_data:
            logger.debug(f"[{symbol}] 日足なし → スキップ")
            continue

        df_daily = daily_data[symbol]
        atr_series = calc_atr(df_daily, period=14)

        trading_dates = df5.index.normalize().unique()

        for trade_date in trading_dates:
            # この日のスクリーナー選定リストを確認
            date_str = str(trade_date.date())
            if date_str not in day_symbols:
                continue
            if symbol not in day_symbols[date_str]:
                continue  # 選ばれていない日はスキップ

            day_df = df5[df5.index.normalize() == trade_date]
            if len(day_df) < 6:
                continue

            # 前日終値・ATR・値幅制限
            daily_before = df_daily[df_daily.index.normalize() < trade_date]
            if len(daily_before) < 15:
                continue
            prev_close = daily_before["close"].iloc[-1]
            today_atr = atr_series.loc[daily_before.index[-1]]
            limit_low, limit_high = calc_daily_limit(prev_close)

            # 平均出来高（日足20日移動平均）
            avg_daily_vol = (
                df_daily["volume"].iloc[-20:].mean()
                if len(df_daily) >= 20
                else df_daily["volume"].mean()
            )

            # ORBレンジ計算
            orb = bt._calc_orb_range(day_df)
            if orb is None:
                continue

            # フィルター適用
            skip_reason = bt._apply_filters(
                orb, today_atr, avg_daily_vol, limit_low, limit_high, prev_close
            )
            if skip_reason:
                logger.debug(f"[{symbol}][{date_str}] フィルター除外: {skip_reason}")
                continue

            # トレードシミュレーション
            trade = bt._simulate_trade(
                symbol, trade_date, day_df, orb, limit_low, limit_high, prev_close
            )
            if trade:
                bt.trades.append(trade)

    total = len(bt.trades)
    logger.info(f"[{label}] 合計 {total} 取引")
    return bt._calc_results()


# ─── 比較表示 ─────────────────────────────────────────────────────────

def print_comparison(all_results: Dict[str, dict]) -> None:
    """3パターンの比較サマリーを表示"""
    order = ["baseline", "screened_3", "screened_5"]
    labels = {
        "baseline":   "固定10銘柄",
        "screened_3": "スクリーナー3点↑",
        "screened_5": "スクリーナー5点↑",
    }

    print("\n" + "=" * 72)
    print("  スクリーニング統合バックテスト 比較結果")
    print("=" * 72)

    # ヘッダー行
    print(f"  {'指標':22}", end="")
    for key in order:
        print(f"  {labels[key]:>16}", end="")
    print()
    print("  " + "-" * 70)

    metrics = [
        ("取引数",          "total_trades",       "{:.0f}"),
        ("勝率",            "win_rate",            "{:.1f}%"),
        ("純損益",          "total_pnl",           "¥{:,.0f}"),
        ("PF（損益比）",    "profit_factor",       "{:.2f}"),
        ("期待値/取引",     "expectancy",          "¥{:,.0f}"),
        ("最大ドローダウン","max_drawdown",        "¥{:,.0f}"),
        ("最大DD%",         "max_drawdown_pct",    "{:.1f}%"),
        ("平均保有(分)",    "avg_hold_minutes",    "{:.0f}分"),
    ]

    for display_label, key, fmt in metrics:
        print(f"  {display_label:22}", end="")
        for result_key in order:
            s = all_results.get(result_key, {}).get("summary", {})
            val = s.get(key)
            if val is None:
                print(f"  {'N/A':>16}", end="")
            elif isinstance(val, float) and val == float("inf"):
                print(f"  {'∞':>16}", end="")
            else:
                try:
                    formatted = fmt.format(val)
                except Exception:
                    formatted = str(val)
                print(f"  {formatted:>16}", end="")
        print()

    print("  " + "-" * 70)
    print("  エグジット理由内訳:")
    for result_key in order:
        s = all_results.get(result_key, {}).get("summary", {})
        reasons = s.get("exit_reasons", {})
        total = s.get("total_trades", 0)
        if total == 0:
            continue
        print(f"\n    [{labels[result_key]}]")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            pct = count / total * 100
            print(f"      {reason:15s}: {count:>4}回 ({pct:>5.1f}%)")

    print("\n" + "=" * 72)

    # 月次損益（スクリーナー vs ベースラインの差分表示）
    months = sorted(
        set(
            list(all_results.get("baseline", {}).get("monthly", {}).keys())
            + list(all_results.get("screened_3", {}).get("monthly", {}).keys())
        )
    )
    if months:
        print("\n  月次損益比較（固定 / 3点↑ / 5点↑）:")
        print(f"  {'月':>8}  {'固定10銘柄':>12}  {'3点↑':>12}  {'5点↑':>12}")
        print("  " + "-" * 50)
        for m in months:
            b = all_results.get("baseline", {}).get("monthly", {}).get(m, 0)
            s3 = all_results.get("screened_3", {}).get("monthly", {}).get(m, 0)
            s5 = all_results.get("screened_5", {}).get("monthly", {}).get(m, 0)
            print(f"  {m:>8}  {b:>+12,.0f}  {s3:>+12,.0f}  {s5:>+12,.0f}")
        print()


# ─── メイン ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="スクリーニング統合バックテスト")
    parser.add_argument("--rr", type=float, default=2.0, help="RR比（デフォルト2.0）")
    parser.add_argument("--capital", type=float, default=500_000, help="資金（デフォルト50万）")
    parser.add_argument("--no-cache", action="store_true", help="キャッシュ無視して再取得")
    args = parser.parse_args()

    use_cache = not args.no_cache
    cfg = BacktestConfig(capital=args.capital, rr_ratio=args.rr)

    fetcher = TSEDataFetcher()

    # ──────────────────────────────────────────────────────────────
    # Step 1: データ取得
    # TSEDataFetcher は全てのデータを JST-aware に統一するため TZ 比較が安全
    # ──────────────────────────────────────────────────────────────
    logger.info("=" * 55)
    logger.info("Step 1/4: 全銘柄データ取得")
    logger.info("=" * 55)

    logger.info(f"5分足データ取得: {len(ALL_CANDIDATE_CODES)}銘柄（最大60日分）")
    all_intraday = fetcher.fetch_universe(ALL_CANDIDATE_CODES, interval="5m", use_cache=use_cache)

    logger.info(f"日足データ取得: {len(ALL_CANDIDATE_CODES)}銘柄（2年分）")
    all_daily = fetcher.fetch_universe(ALL_CANDIDATE_CODES, interval="1d", use_cache=use_cache)

    if not all_intraday:
        logger.error("5分足データが取得できませんでした。終了します。")
        sys.exit(1)

    # ──────────────────────────────────────────────────────────────
    # Step 2: インデックス履歴取得
    # ──────────────────────────────────────────────────────────────
    logger.info("=" * 55)
    logger.info("Step 2/4: インデックス履歴取得")
    logger.info("=" * 55)

    nk_hist, jpy_hist, vix_hist = fetch_index_history()

    # ──────────────────────────────────────────────────────────────
    # Step 3: 取引日リスト → スクリーニング実施
    # ──────────────────────────────────────────────────────────────
    logger.info("=" * 55)
    logger.info("Step 3/4: 日次スクリーニング実行")
    logger.info("=" * 55)

    # 全銘柄の5分足に含まれる取引日を収集
    trading_dates_set: set = set()
    for df5 in all_intraday.values():
        trading_dates_set.update(df5.index.normalize().unique().tolist())
    trading_dates = sorted(trading_dates_set)
    logger.info(f"バックテスト期間: {trading_dates[0].date()} 〜 {trading_dates[-1].date()} "
                f"({len(trading_dates)}日)")

    # スクリーナー選定（min_score=3.0 / 5.0）
    day_symbols_3 = build_day_symbols(
        all_daily_data=all_daily,
        trading_dates=trading_dates,
        nk_hist=nk_hist,
        jpy_hist=jpy_hist,
        vix_hist=vix_hist,
        min_score=3.0,
        top_n=8,
    )
    day_symbols_5 = build_day_symbols(
        all_daily_data=all_daily,
        trading_dates=trading_dates,
        nk_hist=nk_hist,
        jpy_hist=jpy_hist,
        vix_hist=vix_hist,
        min_score=5.0,
        top_n=8,
    )

    # ──────────────────────────────────────────────────────────────
    # Step 4: バックテスト実行（3パターン）
    # ──────────────────────────────────────────────────────────────
    logger.info("=" * 55)
    logger.info("Step 4/4: バックテスト実行（3パターン）")
    logger.info("=" * 55)

    all_results: Dict[str, dict] = {}

    # ① 固定10銘柄ベースライン
    # 全日付で BASELINE_SYMBOLS を全て対象にする（スクリーニングなし）
    baseline_day_symbols = {
        str(d.date()): BASELINE_SYMBOLS for d in trading_dates
    }
    baseline_intraday = {k: v for k, v in all_intraday.items() if k in BASELINE_SYMBOLS}
    baseline_daily = {k: v for k, v in all_daily.items() if k in BASELINE_SYMBOLS}

    logger.info("--- ① 固定10銘柄 ベースライン ---")
    all_results["baseline"] = run_backtest_with_symbols(
        label="固定10銘柄",
        intraday_data=baseline_intraday,
        daily_data=baseline_daily,
        day_symbols=baseline_day_symbols,
        config=BacktestConfig(
            capital=cfg.capital,
            rr_ratio=cfg.rr_ratio,
            symbols=BASELINE_SYMBOLS,
        ),
    )

    # ② スクリーナー選別（3点以上）
    logger.info("--- ② スクリーナー（3点以上） ---")
    all_results["screened_3"] = run_backtest_with_symbols(
        label="スクリーナー3点↑",
        intraday_data=all_intraday,
        daily_data=all_daily,
        day_symbols=day_symbols_3,
        config=cfg,
    )

    # ③ スクリーナー選別（5点以上）
    logger.info("--- ③ スクリーナー（5点以上） ---")
    all_results["screened_5"] = run_backtest_with_symbols(
        label="スクリーナー5点↑",
        intraday_data=all_intraday,
        daily_data=all_daily,
        day_symbols=day_symbols_5,
        config=cfg,
    )

    # ──────────────────────────────────────────────────────────────
    # 比較表示
    # ──────────────────────────────────────────────────────────────
    print_comparison(all_results)

    # スクリーナー頻出銘柄 TOP10 を表示
    _print_screener_stats(day_symbols_3, "スクリーナー3点↑ 頻出銘柄")
    _print_screener_stats(day_symbols_5, "スクリーナー5点↑ 頻出銘柄")


def _print_screener_stats(day_symbols: Dict[str, List[str]], title: str) -> None:
    """スクリーナーが各銘柄を選んだ回数のランキングを表示"""
    from collections import Counter
    counter: Counter = Counter()
    for codes in day_symbols.values():
        counter.update(codes)
    if not counter:
        return

    print(f"\n  {title} (選出回数順):")
    print(f"  {'銘柄':>6}  {'名前':>12}  {'選出日数':>8}")
    for code, cnt in counter.most_common(10):
        name = CANDIDATE_UNIVERSE.get(code, "?")
        print(f"  {code:>6}  {name:>12}  {cnt:>8}日")
    print()


if __name__ == "__main__":
    main()
