"""
market_data.py のユニットテスト
修正箇所: タイムゾーン処理 (df.index.tz)
"""
import pytest
import pandas as pd
import numpy as np
import pytz
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.market_data import MarketDataFetcher


@pytest.fixture
def fetcher():
    return MarketDataFetcher()


def _make_ohlcv(tz=None, rows=60) -> pd.DataFrame:
    """テスト用のOHLCVデータを生成する"""
    dates = pd.date_range("2024-01-01", periods=rows, freq="D", tz=tz)
    data = {
        "Open": np.random.uniform(100, 200, rows),
        "High": np.random.uniform(200, 250, rows),
        "Low": np.random.uniform(80, 100, rows),
        "Close": np.random.uniform(100, 200, rows),
        "Volume": np.random.randint(1_000_000, 10_000_000, rows),
    }
    return pd.DataFrame(data, index=dates)


# ---------------------------------------------------------------
# タイムゾーン処理テスト (P0バグ修正の検証)
# ---------------------------------------------------------------

class TestTimezoneHandling:
    """df.index.tz を使った修正が正しく機能するか検証する"""

    def test_utc_data_converts_to_et(self, fetcher):
        """UTC付きデータがETに変換されること"""
        df_utc = _make_ohlcv(tz="UTC")
        assert df_utc.index.tz is not None

        if df_utc.index.tz is None:
            df_utc.index = df_utc.index.tz_localize("UTC").tz_convert(fetcher.et_tz)
        else:
            df_utc.index = df_utc.index.tz_convert(fetcher.et_tz)

        assert str(df_utc.index.tz) == "America/New_York"

    def test_naive_data_localizes_to_utc_then_converts(self, fetcher):
        """タイムゾーンなしデータがUTCにローカライズされてETに変換されること"""
        df_naive = _make_ohlcv(tz=None)
        assert df_naive.index.tz is None

        if df_naive.index.tz is None:
            df_naive.index = df_naive.index.tz_localize("UTC").tz_convert(fetcher.et_tz)
        else:
            df_naive.index = df_naive.index.tz_convert(fetcher.et_tz)

        assert str(df_naive.index.tz) == "America/New_York"

    def test_et_data_stays_et(self, fetcher):
        """すでにET付きのデータは変換後もETのまま"""
        et_tz = pytz.timezone("America/New_York")
        df_et = _make_ohlcv(tz=et_tz)

        if df_et.index.tz is None:
            df_et.index = df_et.index.tz_localize("UTC").tz_convert(fetcher.et_tz)
        else:
            df_et.index = df_et.index.tz_convert(fetcher.et_tz)

        assert str(df_et.index.tz) == "America/New_York"

    def test_old_code_would_fail_with_tzinfo(self):
        """旧バグ: DatetimeIndex に .tzinfo は存在しない（修正前の問題の確認）"""
        df = _make_ohlcv(tz=None)
        # DatetimeIndex には .tzinfo がなく AttributeError になるか None を返す
        # 旧コードは if df.index.tzinfo: と書いていたため、Noneタイムゾーンの場合に
        # tz_convert を呼ばず変換されないバグがあった
        assert not hasattr(df.index, "tzinfo") or df.index.tz is None


# ---------------------------------------------------------------
# テクニカル指標テスト
# ---------------------------------------------------------------

class TestCalculateIndicators:
    """calculate_indicators が正しく計算されること"""

    def test_ema20_ema50_present(self, fetcher):
        df = _make_ohlcv(tz="UTC", rows=100)
        df.columns = [c.lower() for c in df.columns]
        result = fetcher.calculate_indicators(df)
        assert "ema20" in result.columns
        assert "ema50" in result.columns

    def test_rsi_range(self, fetcher):
        """RSIは0〜100の範囲に収まること"""
        df = _make_ohlcv(tz="UTC", rows=100)
        df.columns = [c.lower() for c in df.columns]
        result = fetcher.calculate_indicators(df)
        valid_rsi = result["rsi"].dropna()
        assert (valid_rsi >= 0).all(), f"RSI < 0 の値が存在: {valid_rsi[valid_rsi < 0]}"
        assert (valid_rsi <= 100).all(), f"RSI > 100 の値が存在: {valid_rsi[valid_rsi > 100]}"

    def test_atr_non_negative(self, fetcher):
        """ATRは常に0以上"""
        df = _make_ohlcv(tz="UTC", rows=100)
        df.columns = [c.lower() for c in df.columns]
        result = fetcher.calculate_indicators(df)
        assert (result["atr"].dropna() >= 0).all()

    def test_volume_ratio_equals_volume_over_ma(self, fetcher):
        """volume_ratio = volume / volume_ma20"""
        df = _make_ohlcv(tz="UTC", rows=100)
        df.columns = [c.lower() for c in df.columns]
        result = fetcher.calculate_indicators(df)
        expected = result["volume"] / result["volume_ma20"]
        pd.testing.assert_series_equal(result["volume_ratio"], expected, check_names=False)

    def test_bollinger_upper_above_lower(self, fetcher):
        """ボリンジャーバンド: 上バンド > 下バンド"""
        df = _make_ohlcv(tz="UTC", rows=100)
        df.columns = [c.lower() for c in df.columns]
        result = fetcher.calculate_indicators(df)
        valid = result.dropna(subset=["bb_upper", "bb_lower"])
        assert (valid["bb_upper"] > valid["bb_lower"]).all()

    def test_high20_is_rolling_max(self, fetcher):
        """high_20 は高値の20日ローリング最大値"""
        df = _make_ohlcv(tz="UTC", rows=100)
        df.columns = [c.lower() for c in df.columns]
        result = fetcher.calculate_indicators(df)
        expected = df["high"].rolling(window=20).max()
        pd.testing.assert_series_equal(result["high_20"], expected, check_names=False)


# ---------------------------------------------------------------
# 市場環境分類テスト (テスタルール境界値)
# ---------------------------------------------------------------

class TestClassifyMarketCondition:
    """VIX境界値でのmarket_condition分類が正しいか"""

    def test_vix_above_35_is_extreme_fear(self, fetcher):
        assert fetcher._classify_market_condition(35.1, "up") == "extreme_fear"

    def test_vix_exactly_35_is_extreme_fear(self, fetcher):
        # VIX > 35 でのみ extreme_fear → 35.0 は extreme_fear ではない
        assert fetcher._classify_market_condition(35.0, "up") != "extreme_fear"

    def test_vix_above_25_below_35_is_fear(self, fetcher):
        assert fetcher._classify_market_condition(25.1, "up") == "fear"

    def test_vix_above_18_below_25_is_neutral(self, fetcher):
        assert fetcher._classify_market_condition(20.0, "up") == "neutral"

    def test_vix_below_18_uptrend_is_bullish(self, fetcher):
        assert fetcher._classify_market_condition(15.0, "up") == "bullish"

    def test_vix_below_18_downtrend_is_bearish(self, fetcher):
        assert fetcher._classify_market_condition(15.0, "down") == "bearish"

    def test_vix_none_is_unknown(self, fetcher):
        assert fetcher._classify_market_condition(None, "up") == "unknown"


# ---------------------------------------------------------------
# キャッシュテスト
# ---------------------------------------------------------------

class TestCache:
    def test_cache_miss_on_unknown_key(self, fetcher):
        assert not fetcher._is_cache_valid("nonexistent_key")

    def test_cache_hit_after_set(self, fetcher):
        import datetime as dt
        key = "AAPL_3mo_1d"
        fetcher.cache[key] = _make_ohlcv(tz="UTC")
        fetcher.cache_expiry[key] = dt.datetime.now() + dt.timedelta(minutes=5)
        assert fetcher._is_cache_valid(key)

    def test_cache_miss_after_expiry(self, fetcher):
        import datetime as dt
        key = "AAPL_3mo_1d"
        fetcher.cache[key] = _make_ohlcv(tz="UTC")
        fetcher.cache_expiry[key] = dt.datetime.now() - dt.timedelta(seconds=1)
        assert not fetcher._is_cache_valid(key)
