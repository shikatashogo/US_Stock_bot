"""
マクロ経済データ取得モジュール
================================
VIX・金利・市場指数・ドル指数等のマクロ指標を取得する。
すべてyfinanceから無料で取得可能。

提供するマクロシグナル:
  - 市場センチメント（VIX水準・トレンド）
  - 金利環境（米国10年債・短期金利・逆イールド）
  - 市場トレンド（S&P500・NASDAQ・日経平均）
  - ドル円・ドル指数（日本株の為替感応度に利用）
"""
from __future__ import annotations

import pickle
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf
from loguru import logger

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "recommend_cache"


class MacroFetcher:
    """マクロ経済データ取得クラス"""

    # 取得対象シンボル
    SYMBOLS = {
        "vix":        "^VIX",       # 恐怖指数
        "sp500":      "^GSPC",      # S&P500
        "nasdaq":     "^IXIC",      # NASDAQ
        "nikkei":     "^N225",      # 日経平均
        "us10y":      "^TNX",       # 米国10年債利回り（%）
        "us3m":       "^IRX",       # 米国3ヶ月短期金利
        "dxy":        "DX-Y.NYB",   # ドル指数
        "usdjpy":     "JPY=X",      # ドル円（USD/JPY）
    }

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"macro_{key}.pkl"

    def _is_fresh(self, path: Path, max_age_hours: float = 4.0) -> bool:
        if not path.exists():
            return False
        age_sec = datetime.now().timestamp() - path.stat().st_mtime
        return age_sec < max_age_hours * 3600

    def _save(self, path: Path, data) -> None:
        with open(path, "wb") as f:
            pickle.dump(data, f)

    def _load(self, path: Path):
        with open(path, "rb") as f:
            return pickle.load(f)

    def fetch_series(
        self, key: str, period: str = "6mo", use_cache: bool = True
    ) -> pd.DataFrame:
        """
        個別マクロ指標の時系列データを取得

        Args:
            key   : SYMBOLS の dict キー（"vix", "sp500" 等）
            period: yfinance period文字列
        """
        if key not in self.SYMBOLS:
            raise ValueError(f"不明なキー: {key}. 有効: {list(self.SYMBOLS.keys())}")

        symbol = self.SYMBOLS[key]
        cache_path = self._cache_path(f"{key}_{period}")

        if use_cache and self._is_fresh(cache_path, max_age_hours=4.0):
            return self._load(cache_path)

        logger.info(f"[マクロ] {key}({symbol}) 取得中...")
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval="1d")
            df.columns = [c.lower() for c in df.columns]
            df = df[["close"]].rename(columns={"close": key})
            df = df[df[key] > 0].sort_index()
            df.index.name = "date"
            self._save(cache_path, df)
            return df
        except Exception as e:
            logger.error(f"[マクロ] {key} 取得失敗: {e}")
            return pd.DataFrame()

    def get_macro_snapshot(self, use_cache: bool = True) -> dict:
        """
        現在のマクロ環境スナップショットを返す

        Returns:
            dict with keys:
              vix_current, vix_30d_avg, vix_regime
              us10y_current, us3m_current, yield_curve_spread
              sp500_trend (1m return %), nasdaq_trend
              nikkei_trend
              usdjpy_current
              macro_score  : -2 〜 +2（プラスが強気環境）
              macro_summary: 自然文サマリー
        """
        cache_path = self._cache_path("snapshot")
        if use_cache and self._is_fresh(cache_path, max_age_hours=4.0):
            return self._load(cache_path)

        result: dict = {}

        # VIX
        vix_df = self.fetch_series("vix", period="3mo", use_cache=use_cache)
        if not vix_df.empty:
            result["vix_current"] = round(float(vix_df["vix"].iloc[-1]), 2)
            result["vix_30d_avg"] = round(float(vix_df["vix"].tail(30).mean()), 2)
            v = result["vix_current"]
            if v < 15:
                result["vix_regime"] = "低ボラ（強気）"
            elif v < 20:
                result["vix_regime"] = "平常（中立）"
            elif v < 30:
                result["vix_regime"] = "警戒（弱気）"
            else:
                result["vix_regime"] = "パニック（リスクオフ）"
        else:
            result["vix_current"] = None
            result["vix_regime"] = "取得不可"

        # 米国金利
        us10y_df = self.fetch_series("us10y", period="3mo", use_cache=use_cache)
        us3m_df  = self.fetch_series("us3m",  period="3mo", use_cache=use_cache)
        if not us10y_df.empty:
            result["us10y_current"] = round(float(us10y_df["us10y"].iloc[-1]), 3)
        else:
            result["us10y_current"] = None
        if not us3m_df.empty:
            result["us3m_current"] = round(float(us3m_df["us3m"].iloc[-1]), 3)
        else:
            result["us3m_current"] = None

        # イールドカーブ（長短金利差）
        if result.get("us10y_current") and result.get("us3m_current"):
            spread = result["us10y_current"] - result["us3m_current"]
            result["yield_curve_spread"] = round(spread, 3)
            result["yield_curve_inverted"] = spread < 0
        else:
            result["yield_curve_spread"] = None
            result["yield_curve_inverted"] = None

        # 市場トレンド（1ヶ月リターン）
        for key, label in [("sp500", "sp500_trend"), ("nasdaq", "nasdaq_trend"), ("nikkei", "nikkei_trend")]:
            df = self.fetch_series(key, period="3mo", use_cache=use_cache)
            if not df.empty and len(df) >= 21:
                current = float(df[key].iloc[-1])
                month_ago = float(df[key].iloc[-21])
                pct = (current - month_ago) / month_ago * 100
                result[label] = round(pct, 2)
            else:
                result[label] = None

        # ドル円
        usdjpy_df = self.fetch_series("usdjpy", period="1mo", use_cache=use_cache)
        if not usdjpy_df.empty:
            result["usdjpy_current"] = round(float(usdjpy_df["usdjpy"].iloc[-1]), 2)
        else:
            result["usdjpy_current"] = None

        # マクロスコア計算（-2 〜 +2）
        result["macro_score"] = self._calc_macro_score(result)
        result["macro_summary"] = self._build_summary(result)

        self._save(cache_path, result)
        return result

    def _calc_macro_score(self, snap: dict) -> float:
        """マクロ環境を数値スコア化（プラス=投資好環境）"""
        score = 0.0

        # VIX スコア
        vix = snap.get("vix_current")
        if vix is not None:
            if vix < 15:    score += 0.8
            elif vix < 20:  score += 0.3
            elif vix < 30:  score -= 0.5
            else:           score -= 1.5

        # S&P500 トレンド
        sp = snap.get("sp500_trend")
        if sp is not None:
            if sp > 3:    score += 0.6
            elif sp > 0:  score += 0.2
            elif sp > -3: score -= 0.3
            else:         score -= 0.8

        # イールドカーブ
        inverted = snap.get("yield_curve_inverted")
        if inverted is True:
            score -= 0.5   # 逆イールド = 景気後退リスク
        elif inverted is False:
            score += 0.3

        return round(max(-2.0, min(2.0, score)), 2)

    def _build_summary(self, snap: dict) -> str:
        """マクロ環境の自然文サマリーを生成"""
        lines = []

        vix = snap.get("vix_current")
        regime = snap.get("vix_regime", "")
        if vix:
            lines.append(f"VIX {vix:.1f}（{regime}）")

        us10y = snap.get("us10y_current")
        if us10y:
            lines.append(f"米国10年債利回り {us10y:.2f}%")

        inverted = snap.get("yield_curve_inverted")
        if inverted is True:
            lines.append("イールドカーブ逆転中（景気後退リスクあり）")
        elif inverted is False:
            lines.append("イールドカーブ正常（スプレッド+" + f"{snap.get('yield_curve_spread',0):.2f}%）")

        sp = snap.get("sp500_trend")
        if sp is not None:
            direction = "↑" if sp > 0 else "↓"
            lines.append(f"S&P500 1ヶ月 {direction}{abs(sp):.1f}%")

        nikkei = snap.get("nikkei_trend")
        if nikkei is not None:
            direction = "↑" if nikkei > 0 else "↓"
            lines.append(f"日経平均 1ヶ月 {direction}{abs(nikkei):.1f}%")

        usdjpy = snap.get("usdjpy_current")
        if usdjpy:
            lines.append(f"ドル円 {usdjpy:.1f}円")

        score = snap.get("macro_score", 0)
        env_label = "強気" if score > 0.5 else "弱気" if score < -0.5 else "中立"
        lines.append(f"総合マクロ評価: {env_label}（スコア{score:+.1f}）")

        return " / ".join(lines)
