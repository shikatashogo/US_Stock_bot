"""
分析パイプライン（共通モジュール）
====================================
app.py / notify_job.py / recommend.py から共通利用する
銘柄分析の一連の処理をまとめたモジュール。

重複実装を防ぐため、パイプラインはここ1ヶ所に定義する。
"""
from __future__ import annotations

from loguru import logger

from config.universe import is_japan_stock
from src.analysis.fundamental import FundamentalAnalyzer
from src.analysis.screener import StockScreener, filter_recommendations
from src.analysis.technical import TechnicalAnalyzer
from src.analysis.valuation import ValuationCalculator
from src.data.macro_fetcher import MacroFetcher
from src.data.stock_fetcher import StockFetcher


def run_pipeline(
    symbols: list[str],
    use_cache: bool = True,
    top_n: int | None = None,
) -> tuple[list, dict]:
    """
    銘柄リストを受け取り、推奨候補とマクロスナップショットを返す。

    Args:
        symbols  : 分析対象銘柄コードのリスト
        use_cache: True = pickleキャッシュを使用（高速）
        top_n    : 返す推奨銘柄数の上限（None = 全件）

    Returns:
        (recommended: list[Candidate], macro_snap: dict)
    """
    fetcher = StockFetcher()
    macro   = MacroFetcher()

    logger.info(f"分析開始: {len(symbols)}銘柄")

    macro_snap  = macro.get_macro_snapshot(use_cache=use_cache)
    price_data  = fetcher.fetch_universe_prices(symbols, use_cache=use_cache)
    fd_raw_dict = fetcher.fetch_universe_fundamentals(symbols, use_cache=use_cache)

    if not fd_raw_dict:
        logger.error("財務データ取得失敗")
        return [], macro_snap

    fa = FundamentalAnalyzer()
    ta = TechnicalAnalyzer()
    vc = ValuationCalculator()

    fd_scores  = {s: fa.analyze(fd) for s, fd in fd_raw_dict.items()}
    valuations = {s: vc.calculate(fd) for s, fd in fd_raw_dict.items()}

    # 相対強度計算のため、日本株はNikkei・米国株はS&P500をベンチマークとして渡す
    sp500_1m  = macro_snap.get("sp500_trend")   # 単位: %
    nikkei_1m = macro_snap.get("nikkei_trend")  # 単位: %
    tech_signals = {
        s: ta.analyze(
            s, df,
            benchmark_1m=nikkei_1m if is_japan_stock(s) else sp500_1m,
        )
        for s, df in price_data.items()
    }

    screener   = StockScreener()
    candidates = screener.screen(
        fundamentals=fd_scores,
        technicals=tech_signals,
        valuations=valuations,
        raw_fd=fd_raw_dict,
        macro_snap=macro_snap,
    )
    recommended = filter_recommendations(candidates)
    logger.info(f"推奨銘柄: {len(recommended)}銘柄")

    if top_n is not None:
        recommended = recommended[:top_n]

    return recommended, macro_snap
