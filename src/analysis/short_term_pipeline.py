"""
短期モメンタムスクリーニング パイプライン
==========================================
Step1: yfinance で全対象銘柄のテクニカル・PEAD データを取得
Step2: 一次スクリーニングで上位30銘柄に絞る（MarketAux節約）
Step3: MarketAux でニュースセンチメントを取得（上位30銘柄のみ）
Step4: センチメント込みで最終スクリーニング

この順序により、MarketAux の 100リクエスト/日制限を最小消費で運用する。
"""
from __future__ import annotations

from loguru import logger

from src.data.stock_fetcher import StockFetcher
from src.data.short_term_fetcher import ShortTermFetcher
from src.data.news_fetcher import NewsFetcher
from src.analysis.short_term_screener import ShortTermScreener, ShortTermResult, PASS_THRESHOLD

# Step2 の一次スクリーニングで残す銘柄数（MarketAux への入力を抑制）
PRE_SCREEN_TOP_N = 30


def run_short_term_pipeline(
    symbols: list[str],
    use_cache: bool = True,
    max_workers: int = 8,
) -> list[ShortTermResult]:
    """
    短期モメンタムスクリーニングを実行する。

    Args:
        symbols:     対象銘柄リスト（米国株推奨）
        use_cache:   True = 各キャッシュを使用（yfinance 24h / MarketAux 12h）
        max_workers: 並列取得スレッド数

    Returns:
        ShortTermResult のリスト（スコア降順、50点以上のみ）
    """
    logger.info(f"短期スクリーニング パイプライン開始: {len(symbols)}銘柄")

    stock_fetcher = StockFetcher()
    st_fetcher    = ShortTermFetcher()
    news_fetcher  = NewsFetcher()
    screener      = ShortTermScreener()

    # ── Step1: 基本財務データ（yfinance.info） ──────────────────────
    logger.info("Step1: 基本財務データ取得（yfinance.info）")
    fd_dict = stock_fetcher.fetch_universe_fundamentals(
        symbols,
        use_cache=use_cache,
        max_workers=max_workers,
    )

    # ── Step2: 短期テクニカル・PEAD データ（yfinance 詳細） ─────────
    logger.info("Step2: 短期テクニカル・PEADデータ取得")
    raw_dict = st_fetcher.fetch_universe(
        symbols,
        fd_dict=fd_dict,
        use_cache=use_cache,
        max_workers=max_workers,
    )

    # ── Step3: 一次スクリーニング（センチメントなし）────────────────
    # MarketAux の節約のため、まずセンチメントを除いてスコアリングし
    # 上位 PRE_SCREEN_TOP_N 銘柄のみ次のステップへ進める
    logger.info("Step3: 一次スクリーニング（センチメント除外）")
    pre_results  = screener.screen(raw_dict, sentiment_dict={})
    top_symbols  = [r.symbol for r in pre_results[:PRE_SCREEN_TOP_N]]
    logger.info(
        f"一次通過: {len(top_symbols)}銘柄 → MarketAux センチメント取得対象"
    )

    # ── Step4: MarketAux ニュースセンチメント（上位銘柄のみ） ────────
    logger.info("Step4: ニュースセンチメント取得（MarketAux）")
    sentiment_dict: dict[str, float] = {}
    if top_symbols:
        sentiment_dict = news_fetcher.fetch_sentiment(
            top_symbols, use_cache=use_cache
        )
        logger.info(
            f"センチメント取得: {len(sentiment_dict)}/{len(top_symbols)}銘柄"
        )

    # ── Step5: 最終スクリーニング（センチメント込み） ────────────────
    logger.info("Step5: 最終スクリーニング（センチメント統合）")
    final_results = screener.screen(raw_dict, sentiment_dict)

    logger.info(
        f"短期スクリーニング完了: {len(final_results)}銘柄が{PASS_THRESHOLD}点以上"
    )
    return final_results
