"""
取引対象銘柄ユニバース管理モジュール
流動性・出来高・市場環境を考慮して取引対象を動的に選別する
"""
from typing import List, Dict, Optional
import pandas as pd
import yfinance as yf
from loguru import logger

from .market_data import MarketDataFetcher


class UniverseManager:
    """取引対象銘柄を管理・フィルタリングするクラス"""

    def __init__(self, settings: dict, data_fetcher: MarketDataFetcher):
        self.settings = settings
        self.data = data_fetcher
        self.primary_symbols = settings["universe"]["primary"]
        self.rules = settings["testa_rules"]["entry"]

    def get_tradable_symbols(self, mode: str = "swing") -> List[str]:
        """
        テスタルールに基づいてその日取引可能な銘柄を返す
        mode: "day" (デイトレ) または "swing" (スイング)
        """
        candidates = self.primary_symbols.copy()
        # ETFはデイトレ/スイングともにユニバースから除外
        candidates = [s for s in candidates if s not in ("SPY", "QQQ")]

        tradable = []
        for symbol in candidates:
            if self._passes_filters(symbol):
                tradable.append(symbol)

        logger.info(f"取引可能銘柄: {len(tradable)}/{len(candidates)}銘柄 [{mode}モード]")
        return tradable

    def _passes_filters(self, symbol: str) -> bool:
        """テスタルールのフィルターを通過するか判定する"""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info

            # 最低出来高チェック（流動性重視）
            avg_volume = getattr(info, "three_month_average_volume", 0) or 0
            if avg_volume < self.rules["min_daily_volume"]:
                logger.debug(f"{symbol}: 出来高不足 ({avg_volume:,.0f} < {self.rules['min_daily_volume']:,.0f})")
                return False

            # 最低株価チェック
            price = getattr(info, "last_price", 0) or 0
            if price < self.rules["min_price"]:
                logger.debug(f"{symbol}: 株価が低すぎる (${price:.2f} < ${self.rules['min_price']:.2f})")
                return False

            return True

        except Exception as e:
            logger.warning(f"{symbol} フィルターチェックエラー: {e}")
            return True  # エラー時は含める（保守的に）

    def screen_momentum_stocks(self, min_momentum_pct: float = 2.0) -> List[Dict]:
        """
        モメンタムの強い銘柄をスクリーニングする
        テスタ氏の「強い銘柄を買う」原則に基づく
        """
        results = []
        for symbol in self.primary_symbols:
            if symbol in ("SPY", "QQQ", "^VIX"):
                continue

            df = self.data.get_historical_data(symbol, period="1mo", interval="1d")
            if df is None or len(df) < 5:
                continue

            df = self.data.calculate_indicators(df)
            last = df.iloc[-1]
            prev_5 = df.iloc[-6] if len(df) >= 6 else df.iloc[0]

            momentum_5d = ((last["close"] - prev_5["close"]) / prev_5["close"]) * 100
            volume_ratio = last["volume_ratio"] if "volume_ratio" in df.columns else 1.0

            above_ema20 = last["close"] > last["ema20"] if "ema20" in df.columns else False
            above_ema50 = last["close"] > last["ema50"] if "ema50" in df.columns else False

            if momentum_5d >= min_momentum_pct:
                results.append({
                    "symbol": symbol,
                    "price": round(last["close"], 2),
                    "momentum_5d_pct": round(momentum_5d, 2),
                    "volume_ratio": round(volume_ratio, 2),
                    "above_ema20": above_ema20,
                    "above_ema50": above_ema50,
                    "score": self._calculate_score(momentum_5d, volume_ratio, above_ema20, above_ema50),
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        logger.info(f"モメンタム銘柄スクリーニング完了: {len(results)}件")
        return results

    def screen_breakout_candidates(self) -> List[Dict]:
        """
        ブレイクアウト候補銘柄をスクリーニングする
        20日高値に接近 + 出来高増加
        """
        results = []
        for symbol in self.primary_symbols:
            if symbol in ("SPY", "QQQ", "^VIX"):
                continue

            df = self.data.get_historical_data(symbol, period="2mo", interval="1d")
            if df is None or len(df) < 21:
                continue

            df = self.data.calculate_indicators(df)
            last = df.iloc[-1]

            if "high_20" not in df.columns:
                continue

            # 高値の95%以上に株価がある = ブレイクアウト直前
            near_high = last["close"] >= last["high_20"] * 0.95
            volume_increasing = last.get("volume_ratio", 1.0) >= 1.3

            if near_high and volume_increasing:
                results.append({
                    "symbol": symbol,
                    "price": round(last["close"], 2),
                    "resistance_20d": round(last["high_20"], 2),
                    "pct_from_high": round((last["high_20"] - last["close"]) / last["high_20"] * 100, 2),
                    "volume_ratio": round(last.get("volume_ratio", 1.0), 2),
                })

        results.sort(key=lambda x: x["volume_ratio"], reverse=True)
        logger.info(f"ブレイクアウト候補: {len(results)}件")
        return results

    def get_market_leaders(self) -> List[str]:
        """
        当日のマーケットリーダー（市場を牽引している銘柄）を特定する
        テスタ氏の「強い銘柄を追え」原則
        """
        today_movers = []
        for symbol in self.primary_symbols:
            if symbol in ("SPY", "QQQ", "^VIX"):
                continue

            df = self.data.get_intraday_data(symbol, days=1, interval="5m")
            if df is None or len(df) < 2:
                continue

            try:
                open_price = df["open"].iloc[0]
                current_price = df["close"].iloc[-1]
                change_pct = (current_price - open_price) / open_price * 100
                volume_sum = df["volume"].sum()

                today_movers.append({
                    "symbol": symbol,
                    "change_pct": round(change_pct, 2),
                    "volume": volume_sum,
                })
            except Exception:
                continue

        today_movers.sort(key=lambda x: x["change_pct"], reverse=True)
        leaders = [m["symbol"] for m in today_movers[:3] if m["change_pct"] > 1.0]
        return leaders

    def _calculate_score(
        self,
        momentum: float,
        volume_ratio: float,
        above_ema20: bool,
        above_ema50: bool,
    ) -> float:
        """取引優先度スコアを計算する（高いほど優先）"""
        score = momentum * 0.4
        score += (volume_ratio - 1.0) * 20 * 0.3
        score += 10 if above_ema20 else 0
        score += 15 if above_ema50 else 0
        return score
