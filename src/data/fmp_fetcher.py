"""
EPSサプライズ取得モジュール
===========================
過去4〜8四半期のEPSサプライズ（実績vs予想）を取得し、
決算発表の「上振れ傾向」をスコアリングする。

データソース: yfinance（earnings_history）
  → 完全無料・APIキー不要
  → 直近約4四半期のEPS実績/予想/サプライズ率を取得

FMPへの移行メモ:
  FMPの無料プラン（2025年時点）はAPI制限が強化されており
  earnings-surprisesエンドポイントは有料プランのみ。
  将来的に上位プランを契約した場合は下記クラス内を
  HTTP取得に差し替えることで対応可能。

EPSサプライズの読み方:
  beat率 ≥ 75% = 上振れ癖あり → 強気サイン
  beat率 ≤ 30% = 下振れ癖あり → 弱気サイン
  平均サプライズ% > 0 = アナリスト予想を平均的に上回る
"""
from __future__ import annotations

import pickle
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "recommend_cache"


class FmpFetcher:
    """
    EPSサプライズデータを取得するクラス（yfinanceベース）

    クラス名は後方互換性のため FmpFetcher のまま維持。
    """

    def __init__(
        self,
        api_key:   Optional[str] = None,   # 将来のFMP移行用（現在は未使用）
        cache_dir: Optional[Path] = None,
    ):
        self.api_key   = api_key  # 将来の有料プラン対応用
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def is_configured(self) -> bool:
        """常にTrue（yfinanceは設定不要）"""
        return True

    # ─── キャッシュ管理 ────────────────────────────────────────────

    def _cache_path(self, key: str) -> Path:
        safe = key.replace("/", "_").replace("^", "_")
        return self.cache_dir / f"eps_{safe}.pkl"

    def _is_fresh(self, path: Path, max_age_days: float = 7.0) -> bool:
        if not path.exists():
            return False
        age_sec = datetime.now().timestamp() - path.stat().st_mtime
        return age_sec < max_age_days * 86400

    def _save(self, path: Path, data) -> None:
        with open(path, "wb") as f:
            pickle.dump(data, f)

    def _load(self, path: Path):
        with open(path, "rb") as f:
            return pickle.load(f)

    # ─── EPSサプライズ取得 ─────────────────────────────────────────

    def fetch_eps_surprise(self, ticker: str, use_cache: bool = True) -> dict:
        """
        直近4〜8四半期のEPSサプライズを取得して集計

        yfinance の earnings_history から取得:
          - epsActual      : 実績EPS
          - epsEstimate    : 予想EPS
          - surprisePercent: サプライズ率（小数。例: 0.1012 = +10.12%）

        Returns:
            {
              "eps_beat_count":       int,   # 予想を上回った四半期数
              "eps_miss_count":       int,   # 予想を下回った四半期数
              "eps_total_quarters":   int,   # 有効な集計四半期数
              "eps_beat_rate":        float, # beat率（0.0〜1.0）、データ不足はNone
              "eps_avg_surprise_pct": float, # 平均サプライズ率（%）、データ不足はNone
            }
        """
        empty = {
            "eps_beat_count":       0,
            "eps_miss_count":       0,
            "eps_total_quarters":   0,
            "eps_beat_rate":        None,
            "eps_avg_surprise_pct": None,
        }

        cache = self._cache_path(ticker)
        if use_cache and self._is_fresh(cache, max_age_days=7):
            return self._load(cache)

        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).earnings_history
        except Exception as e:
            logger.warning(f"[{ticker}] EPS取得失敗: {e}")
            return empty

        if hist is None or hist.empty:
            return empty

        beat_count    = 0
        miss_count    = 0
        surprise_pcts = []

        for _, row in hist.iterrows():
            actual   = row.get("epsActual")
            estimate = row.get("epsEstimate")
            surprise = row.get("surprisePercent")  # 小数 (0.10 = 10%)

            if actual is None or estimate is None:
                continue

            try:
                actual   = float(actual)
                estimate = float(estimate)
            except (ValueError, TypeError):
                continue

            # 赤字予想（推定EPS ≤ 0）はサプライズ率が逆転するため除外
            if estimate <= 0:
                continue

            if surprise is not None:
                try:
                    surprise_pcts.append(float(surprise) * 100)  # %に変換
                except (ValueError, TypeError):
                    pass

            if actual > estimate:
                beat_count += 1
            elif actual < estimate:
                miss_count += 1

        total = beat_count + miss_count
        result = {
            "eps_beat_count":       beat_count,
            "eps_miss_count":       miss_count,
            "eps_total_quarters":   total,
            "eps_beat_rate":        round(beat_count / total, 2) if total > 0 else None,
            "eps_avg_surprise_pct": (
                round(sum(surprise_pcts) / len(surprise_pcts), 1)
                if surprise_pcts else None
            ),
        }

        self._save(cache, result)

        if result["eps_beat_rate"] is not None:
            logger.info(
                f"[{ticker}] EPS beat率: {result['eps_beat_rate']:.0%} "
                f"（{beat_count}/{total}四半期"
                + (f"、平均 {result['eps_avg_surprise_pct']:+.1f}%）" if result["eps_avg_surprise_pct"] else "）")
            )
        return result

    def fetch_universe_eps(
        self,
        symbols:   list[str],
        use_cache: bool = True,
    ) -> dict[str, dict]:
        """複数銘柄のEPSサプライズを取得（逐次処理）"""
        result = {}
        for sym in symbols:
            data = self.fetch_eps_surprise(sym, use_cache=use_cache)
            if data.get("eps_beat_rate") is not None:
                result[sym] = data

        logger.info(f"EPS取得完了: {len(result)}/{len(symbols)} 銘柄")
        return result
