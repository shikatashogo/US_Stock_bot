"""
MarketAux ニュースセンチメント取得モジュール
============================================
MarketAux API（無料プラン: 100リクエスト/日・3記事/リクエスト）から
銘柄ごとのエンティティレベルセンチメントスコアを取得する。

制約:
  - 無料プランは 100 req/day・1リクエストあたり3記事
  - 主に米国株（USティッカー）に有効
  - センチメントスコア: -1.0（強い悲観）〜 +1.0（強い楽観）
  - キャッシュTTL: 12時間（ニュースの鮮度と制限を両立）
"""
from __future__ import annotations

import os
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from loguru import logger

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "recommend_cache"
CACHE_TTL_HOURS = 12.0

MARKETAUX_BASE_URL = "https://api.marketaux.com/v1/news/all"
SYMBOLS_PER_REQUEST = 5   # 1リクエストに含める銘柄数（記事の偏りを防ぐ）
ARTICLES_PER_REQUEST = 3  # 無料プランの上限
LOOKBACK_DAYS = 7         # 過去何日分のニュースを取得するか


class NewsFetcher:
    """MarketAux からニュースセンチメントを取得するクラス"""

    def __init__(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.api_key = os.getenv("MARKETAUX_API_KEY", "")
        if not self.api_key:
            logger.warning("MARKETAUX_API_KEY が未設定です。センチメントは中立スコアで代替します。")

    # ── キャッシュ管理 ──────────────────────────────────────────────

    def _cache_path(self, symbols: list[str]) -> Path:
        key = "_".join(sorted(symbols))
        # キーが長すぎる場合はハッシュで短縮
        if len(key) > 80:
            import hashlib
            key = hashlib.md5(key.encode()).hexdigest()
        return CACHE_DIR / f"news_sentiment_{key}.pkl"

    def _is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        age_sec = datetime.now().timestamp() - path.stat().st_mtime
        return age_sec < CACHE_TTL_HOURS * 3600

    # ── API 取得 ────────────────────────────────────────────────────

    def _fetch_batch(self, symbols: list[str]) -> dict[str, list[float]]:
        """
        指定銘柄群のセンチメントスコアリストを取得する。
        Returns:
            {symbol: [score1, score2, ...]}  # 記事なし → 空リスト
        """
        if not self.api_key:
            return {s: [] for s in symbols}

        params = {
            "symbols":         ",".join(symbols),
            "filter_entities": "true",
            "language":        "en",
            "published_after": (
                datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)
            ).strftime("%Y-%m-%dT%H:%M:%S"),
            "limit":           ARTICLES_PER_REQUEST,
            "api_token":       self.api_key,
        }

        try:
            resp = requests.get(MARKETAUX_BASE_URL, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.HTTPError as e:
            logger.warning(f"MarketAux HTTPエラー: {e}")
            return {s: [] for s in symbols}
        except Exception as e:
            logger.warning(f"MarketAux 取得失敗: {e}")
            return {s: [] for s in symbols}

        # 銘柄ごとにエンティティセンチメントを集計
        scores: dict[str, list[float]] = {s: [] for s in symbols}
        for article in data.get("data", []):
            for entity in article.get("entities", []):
                sym   = (entity.get("symbol") or "").upper()
                score = entity.get("sentiment_score")
                if sym in scores and score is not None:
                    try:
                        scores[sym].append(float(score))
                    except (ValueError, TypeError):
                        pass

        return scores

    def fetch_sentiment(
        self,
        symbols: list[str],
        use_cache: bool = True,
    ) -> dict[str, float]:
        """
        複数銘柄の過去7日間ニュースセンチメント平均を取得する。

        Args:
            symbols: 対象銘柄リスト（USティッカー推奨）
            use_cache: True = 12時間キャッシュを使用

        Returns:
            {symbol: avg_sentiment}  # -1.0〜+1.0
            ※ 記事なしの銘柄はキーに含まれない（None相当）
        """
        cache_path = self._cache_path(symbols)

        if use_cache and self._is_fresh(cache_path):
            logger.debug("ニュースセンチメント: キャッシュ使用")
            with open(cache_path, "rb") as f:
                return pickle.load(f)

        logger.info(f"MarketAux センチメント取得: {len(symbols)}銘柄")

        # SYMBOLS_PER_REQUEST 件ずつバッチ処理
        all_scores: dict[str, list[float]] = {}
        total_requests = 0
        for i in range(0, len(symbols), SYMBOLS_PER_REQUEST):
            batch = symbols[i : i + SYMBOLS_PER_REQUEST]
            batch_scores = self._fetch_batch(batch)
            total_requests += 1
            for sym, sc_list in batch_scores.items():
                all_scores.setdefault(sym, []).extend(sc_list)

        logger.info(f"MarketAux: {total_requests}リクエスト消費")

        # 銘柄ごとに平均スコアを計算
        result: dict[str, float] = {}
        for sym, sc_list in all_scores.items():
            if sc_list:
                result[sym] = round(sum(sc_list) / len(sc_list), 4)

        covered = len(result)
        logger.info(
            f"センチメント取得完了: {covered}/{len(symbols)}銘柄に記事あり"
        )

        with open(cache_path, "wb") as f:
            pickle.dump(result, f)

        return result
