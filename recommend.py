"""
株式推奨Bot メインエントリーポイント
======================================
日本株・米国株を分析し、割安・成長性の高い銘柄を推奨する。
すべてPython（yfinance等）のみで動作し、外部有料APIは使用しない。

実行方法:
  python recommend.py              # 全銘柄分析（日本株＋米国株）
  python recommend.py --jp         # 日本株のみ
  python recommend.py --us         # 米国株のみ
  python recommend.py --top 5      # 上位5銘柄のみ表示
  python recommend.py --refresh    # キャッシュを無視して最新データ取得
  python recommend.py --symbol AAPL 7203  # 特定銘柄のみ分析
  python recommend.py --interactive       # 対話モード

注意事項:
  - 推奨銘柄は「期待値プラスの根拠が複数揃った候補」であり、将来利益の保証ではない
  - 根拠が弱い場合は「推奨なし」と表示する
  - 最終的な投資判断はご自身の責任で行ってください
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from loguru import logger

from config.universe import (
    get_all_symbols, get_japan_symbols, get_us_symbols,
)
from src.analysis.fundamental import FundamentalAnalyzer
from src.analysis.screener import Candidate, StockScreener, filter_recommendations
from src.analysis.technical import TechnicalAnalyzer
from src.analysis.valuation import ValuationCalculator, ValuationResult
from src.data.macro_fetcher import MacroFetcher
from src.data.stock_fetcher import StockFetcher

logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")


# ─── メインパイプライン ──────────────────────────────────────────


def run_analysis(
    symbols: list[str],
    use_cache: bool = True,
    top_n: int = 10,
) -> tuple[list[Candidate], dict]:
    """
    推奨分析パイプラインを実行

    Returns:
        (推奨候補リスト, マクロスナップショット)
    """
    logger.info(f"分析開始: {len(symbols)}銘柄")

    fetcher = StockFetcher()
    macro   = MacroFetcher()

    # ① マクロ環境
    logger.info("① マクロ環境取得中...")
    macro_snap  = macro.get_macro_snapshot(use_cache=use_cache)
    macro_score = macro_snap.get("macro_score", 0.0)
    logger.info(f"   {macro_snap.get('macro_summary', '')}")

    # ② データ取得
    logger.info("② 株価・財務データ取得中...")
    price_data       = fetcher.fetch_universe_prices(symbols, use_cache=use_cache)
    fundamentals_raw = fetcher.fetch_universe_fundamentals(symbols, use_cache=use_cache)
    logger.info(f"   株価: {len(price_data)}銘柄, 財務: {len(fundamentals_raw)}銘柄")

    if not fundamentals_raw:
        logger.error("財務データ取得失敗。ネットワーク接続を確認してください。")
        return [], macro_snap

    # ③ ファンダメンタル分析
    logger.info("③ ファンダメンタル分析中...")
    fa       = FundamentalAnalyzer()
    fd_scores = {sym: fa.analyze(fd) for sym, fd in fundamentals_raw.items()}

    # ④ テクニカル分析
    logger.info("④ テクニカル分析中...")
    ta           = TechnicalAnalyzer()
    tech_signals = {sym: ta.analyze(sym, df) for sym, df in price_data.items()}

    # ⑤ 理論株価計算
    logger.info("⑤ 理論株価計算中...")
    vc         = ValuationCalculator()
    valuations = {sym: vc.calculate(fd) for sym, fd in fundamentals_raw.items()}

    # ⑥ 複合スクリーニング
    logger.info("⑥ スクリーニング中...")
    screener   = StockScreener()
    candidates = screener.screen(
        fundamentals=fd_scores,
        technicals=tech_signals,
        valuations=valuations,
        raw_fd=fundamentals_raw,
        macro_score=macro_score,
    )

    recommended = filter_recommendations(candidates)
    return recommended[:top_n], macro_snap


# ─── レポート表示 ────────────────────────────────────────────────


def print_report(candidates: list[Candidate], valuations: dict, macro_snap: dict) -> None:
    """推奨レポートをターミナルに表示"""
    from datetime import date

    width = 70

    print("\n" + "━" * width)
    print(f"  📊 株式推奨レポート  {date.today()}")
    print("━" * width)
    print(f"\n【マクロ環境】")
    print(f"  {macro_snap.get('macro_summary', 'N/A')}")

    if not candidates:
        print("\n" + "━" * width)
        print("  【推奨銘柄】なし")
        print("  現在の市場・財務データから推奨できる銘柄が見つかりませんでした。")
        print("  次回決算後または市場環境が変化したタイミングで再分析してください。")
        print("━" * width)
        return

    print(f"\n【推奨銘柄】{len(candidates)}銘柄\n")

    for i, c in enumerate(candidates, 1):
        fd  = c.fundamental
        val = c.valuation
        tech = c.technical
        currency = c.currency

        def fmt(v):
            if v is None: return "N/A"
            sym = "¥" if currency == "JPY" else "$"
            return f"{sym}{v:,.0f}" if currency == "JPY" else f"{sym}{v:.2f}"

        upside = val.upside_pct
        upside_str = f"+{upside:.1f}%" if upside and upside >= 0 else (f"{upside:.1f}%" if upside else "N/A")

        icon = "🟢" if "強く" in c.recommendation else "🔵"
        print("─" * width)
        print(f"{icon} 【{i}位】 {c.name} （{c.symbol}）  {c.recommendation} ／ 確度: {c.confidence}")
        print(f"   セクター: {c.sector or '―'}  市場: {c.market or '―'}  スコア: {c.composite_score:.1f}/10")
        print()

        # 株価・理論株価
        print(f"  💴 現在株価  : {fmt(val.current_price)}")
        print(
            f"  📐 理論株価  : {fmt(val.fair_value_low)} 〜 {fmt(val.fair_value_high)}"
            f"（保守的中央値: {fmt(val.fair_value_mid)}）"
        )

        # アナリスト目標が現在株価を上回る場合は両方表示
        analyst_tp = val.take_profit
        if analyst_tp and val.current_price and analyst_tp > val.current_price and upside and upside < 0:
            analyst_upside = (analyst_tp - val.current_price) / val.current_price * 100
            print(f"  📈 上昇余地  : 保守的中央値比 {upside_str} / アナリスト目標まで +{analyst_upside:.1f}%")
        else:
            print(f"  📈 上昇余地  : {upside_str}")

        print(f"  ✂️  損切ライン: {fmt(val.stop_loss)}")
        print(f"  🎯 利確目標  : {fmt(analyst_tp)}")

        if val.method_notes:
            for note in val.method_notes[:2]:
                print(f"     └ {note}")

        # 上昇根拠
        reasons = c.bull_case[:4]
        if reasons:
            print(f"\n  🟢 上昇根拠:")
            for j, r in enumerate(reasons, 1):
                print(f"     {j}. {r}")

        # テクニカル補足
        if tech.rsi_14:
            print(f"\n  📊 テクニカル: RSI {tech.rsi_14:.0f}（{tech.rsi_signal}）  {tech.trend_label}")

        # リスク
        risks = c.key_risks[:2]
        if risks:
            print(f"\n  ⚠️  主要リスク:")
            for r in risks:
                print(f"     ・{r}")

        # 次回決算
        next_earn = c.fundamental.data_quality  # placeholder
        # fd_raw から next_earnings_date を取るには screener経由が必要だが
        # Candidate に next_earnings_date を追加していないため省略

        print()

    print("━" * width)
    print("  ⚠️  免責: 本レポートは情報提供のみを目的とします。")
    print("     利益を保証するものではありません。最終判断は自己責任で。")
    print("━" * width + "\n")


# ─── 対話モード ─────────────────────────────────────────────────


def interactive_mode():
    """対話型インターフェース"""
    print("\n" + "=" * 70)
    print("  株式推奨Bot  （終了: exit）")
    print("=" * 70)

    while True:
        print("\n何を分析しますか？")
        print("  1) 全銘柄（日本株＋米国株）")
        print("  2) 日本株のみ")
        print("  3) 米国株のみ")
        print("  4) 銘柄を指定して分析")
        print("  5) マクロ環境だけ確認")
        print("  q) 終了")
        choice = input("\n> ").strip().lower()

        if choice in ("q", "exit", "quit"):
            print("終了します。")
            break
        elif choice == "1":
            _run_and_print(get_all_symbols())
        elif choice == "2":
            _run_and_print(get_japan_symbols())
        elif choice == "3":
            _run_and_print(get_us_symbols())
        elif choice == "4":
            raw = input("銘柄コードをスペース区切りで入力（例: AAPL 7203 NVDA）: ").strip()
            syms = [s.strip() for s in raw.split() if s.strip()]
            if syms:
                _run_and_print(syms)
        elif choice == "5":
            snap = MacroFetcher().get_macro_snapshot(use_cache=False)
            print(f"\n{snap.get('macro_summary', 'データ取得失敗')}")
        else:
            print("1〜5またはqを入力してください。")


def _run_and_print(symbols: list[str], use_cache: bool = True, top_n: int = 10):
    candidates, macro_snap = run_analysis(symbols, use_cache=use_cache, top_n=top_n)
    # valuationsはcandidates内のval属性に含まれているため別渡し不要
    print_report(candidates, {}, macro_snap)


# ─── エントリーポイント ──────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="株式推奨Bot（完全無料・外部APIなし）"
    )
    parser.add_argument("--jp",          action="store_true", help="日本株のみ")
    parser.add_argument("--us",          action="store_true", help="米国株のみ")
    parser.add_argument("--symbol",      nargs="*",           help="特定銘柄を指定（スペース区切り）")
    parser.add_argument("--top",         type=int, default=10, help="表示する上位N銘柄（デフォルト10）")
    parser.add_argument("--refresh",     action="store_true", help="キャッシュ無視・最新データ取得")
    parser.add_argument("--interactive", action="store_true", help="対話モードで起動")
    args = parser.parse_args()

    if args.interactive:
        interactive_mode()
        return

    if args.symbol:
        symbols = args.symbol
    elif args.jp:
        symbols = get_japan_symbols()
    elif args.us:
        symbols = get_us_symbols()
    else:
        symbols = get_all_symbols()

    candidates, macro_snap = run_analysis(
        symbols, use_cache=not args.refresh, top_n=args.top
    )
    print_report(candidates, {}, macro_snap)


if __name__ == "__main__":
    main()
