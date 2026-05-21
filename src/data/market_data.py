"""
市場データ取得モジュール
リアルタイムデータ: IBKR API
履歴データ: yfinance（バックテスト・スイング分析用）
"""
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import yfinance as yf
import pytz
from loguru import logger


class MarketDataFetcher:
    """市場データの取得・加工を担当するクラス"""

    def __init__(self):
        self.et_tz = pytz.timezone("America/New_York")
        self.cache: Dict[str, pd.DataFrame] = {}
        self.cache_expiry: Dict[str, datetime] = {}
        self.cache_ttl_minutes = 5  # キャッシュ有効期間（分）
        # 市場状態キャッシュ（SPY/QQQ/VIXで4回ティッカー呼び出しが発生するため）
        self._market_status_cache: Optional[Dict] = None
        self._market_status_ts: Optional[datetime] = None
        self._market_status_ttl_seconds = 300

    # ----------------------------------------------------------
    # 履歴データ取得（yfinance）
    # ----------------------------------------------------------

    def get_historical_data(
        self,
        symbol: str,
        period: str = "3mo",
        interval: str = "1d",
        use_cache: bool = True
    ) -> Optional[pd.DataFrame]:
        """
        過去の株価データを取得する
        period: "1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y"
        interval: "1m", "5m", "15m", "30m", "1h", "1d", "1wk"
        """
        cache_key = f"{symbol}_{period}_{interval}"

        if use_cache and self._is_cache_valid(cache_key):
            return self.cache[cache_key]

        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval, auto_adjust=True)

            if df.empty:
                # yfinanceがperiod指定で失敗する場合（例: "possibly delisted"誤検知）
                # 明示的なstart/end日付でリトライする（日足のみ対応）
                if interval == "1d":
                    period_days = {
                        "1mo": 30, "3mo": 90, "6mo": 180,
                        "1y": 365, "2y": 730, "5y": 1825
                    }.get(period, 90)
                    end_dt = datetime.now(self.et_tz)
                    start_dt = end_dt - timedelta(days=period_days + 10)
                    logger.info(
                        f"{symbol}: period='{period}'でデータ取得失敗 → "
                        f"日付指定でリトライ ({start_dt.strftime('%Y-%m-%d')}〜{end_dt.strftime('%Y-%m-%d')})"
                    )
                    df = ticker.history(
                        start=start_dt.strftime("%Y-%m-%d"),
                        end=end_dt.strftime("%Y-%m-%d"),
                        interval=interval,
                        auto_adjust=True,
                    )
                    if not df.empty:
                        logger.info(f"{symbol}: 日付指定リトライ成功 ({len(df)}件)")

            if df.empty:
                logger.warning(f"{symbol}: データが取得できませんでした")
                return None

            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC").tz_convert(self.et_tz)
            else:
                df.index = df.index.tz_convert(self.et_tz)
            df.columns = [c.lower() for c in df.columns]
            df = df[["open", "high", "low", "close", "volume"]].dropna()

            self.cache[cache_key] = df
            self.cache_expiry[cache_key] = datetime.now() + timedelta(minutes=self.cache_ttl_minutes)

            logger.debug(f"{symbol}: {len(df)}件のデータ取得完了 ({interval})")
            return df

        except Exception as e:
            logger.error(f"{symbol} データ取得エラー: {e}")
            return None

    def get_historical_data_by_dates(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        interval: str = "1d",
    ) -> Optional[pd.DataFrame]:
        """開始日・終了日を指定して株価データを取得する（バックテスト用）"""
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date, interval=interval)
            if df.empty:
                return None
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC").tz_convert(self.et_tz)
            else:
                df.index = df.index.tz_convert(self.et_tz)
            df.columns = [c.lower() for c in df.columns]
            df = df[["open", "high", "low", "close", "volume"]].dropna()
            logger.debug(f"{symbol}: {len(df)}件のデータ取得完了 ({start_date}〜{end_date})")
            return df
        except Exception as e:
            logger.error(f"{symbol} 日付指定データ取得エラー: {e}")
            return None

    def get_intraday_data(
        self,
        symbol: str,
        days: int = 1,
        interval: str = "5m"
    ) -> Optional[pd.DataFrame]:
        """当日のイントラデイデータを取得する"""
        period = f"{days}d"
        return self.get_historical_data(symbol, period=period, interval=interval, use_cache=False)

    def get_multi_symbol_data(
        self,
        symbols: List[str],
        period: str = "3mo",
        interval: str = "1d"
    ) -> Dict[str, pd.DataFrame]:
        """複数銘柄のデータを一括取得する"""
        results = {}
        try:
            # yfinanceの一括ダウンロードを使用（効率的）
            tickers = " ".join(symbols)
            raw = yf.download(tickers, period=period, interval=interval, group_by="ticker", progress=False)

            for symbol in symbols:
                try:
                    if len(symbols) == 1:
                        df = raw
                    else:
                        df = raw[symbol]

                    df = df.copy()
                    df.columns = [c.lower() for c in df.columns]
                    df = df[["open", "high", "low", "close", "volume"]].dropna()
                    results[symbol] = df
                except Exception:
                    df = self.get_historical_data(symbol, period, interval)
                    if df is not None:
                        results[symbol] = df

        except Exception as e:
            logger.error(f"一括データ取得エラー: {e}")
            for symbol in symbols:
                df = self.get_historical_data(symbol, period, interval)
                if df is not None:
                    results[symbol] = df

        logger.info(f"{len(results)}/{len(symbols)}銘柄のデータ取得完了")
        return results

    # ----------------------------------------------------------
    # リアルタイム価格取得
    # ----------------------------------------------------------

    def get_current_price(self, symbol: str) -> Optional[float]:
        """現在の株価を取得する"""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            price = info.last_price
            if price and price > 0:
                return round(float(price), 4)
        except Exception:
            pass

        # フォールバック: 直近の終値を使用
        try:
            df = self.get_historical_data(symbol, period="1d", interval="1m", use_cache=False)
            if df is not None and not df.empty:
                return float(df["close"].iloc[-1])
        except Exception as e:
            logger.error(f"{symbol} 現在価格取得エラー: {e}")

        return None

    def get_vix(self) -> Optional[float]:
        """VIX（恐怖指数）の現在値を取得する"""
        return self.get_current_price("^VIX")

    def get_market_status(self) -> Dict:
        """市場全体の状態を取得する（5分キャッシュ：複数銘柄スキャン中の重複呼び防止）"""
        now = datetime.now()
        if (
            self._market_status_cache is not None
            and self._market_status_ts is not None
            and (now - self._market_status_ts).total_seconds() < self._market_status_ttl_seconds
        ):
            return self._market_status_cache

        spy_price = self.get_current_price("SPY")
        qqq_price = self.get_current_price("QQQ")
        vix = self.get_vix()
        spy_trend = self._get_trend("SPY")

        status = {
            "spy_price": spy_price,
            "qqq_price": qqq_price,
            "vix": vix,
            "spy_trend": spy_trend,
            "market_condition": self._classify_market_condition(vix, spy_trend),
            "timestamp": datetime.now(self.et_tz).isoformat(),
        }
        self._market_status_cache = status
        self._market_status_ts = now
        return status

    # ----------------------------------------------------------
    # テクニカル指標の計算
    # ----------------------------------------------------------

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        テスタ戦略に必要なテクニカル指標を計算する
        """
        df = df.copy()

        # 移動平均線
        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
        df["sma200"] = df["close"].rolling(window=200).mean()

        # VWAP（当日のみ有効）
        df["vwap"] = self._calculate_vwap(df)

        # RSI
        df["rsi"] = self._calculate_rsi(df["close"], period=14)

        # ATR（Average True Range）: 損切り幅の計算に使用
        df["atr"] = self._calculate_atr(df, period=14)

        # 出来高移動平均
        df["volume_ma20"] = df["volume"].rolling(window=20).mean()
        df["volume_ratio"] = df["volume"] / df["volume_ma20"]

        # ボリンジャーバンド
        df["bb_middle"] = df["close"].rolling(window=20).mean()
        bb_std = df["close"].rolling(window=20).std()
        df["bb_upper"] = df["bb_middle"] + 2 * bb_std
        df["bb_lower"] = df["bb_middle"] - 2 * bb_std

        # モメンタム
        df["momentum_5"] = df["close"].pct_change(5)
        df["momentum_20"] = df["close"].pct_change(20)

        # 高値・安値ブレイクアウト判定
        df["high_20"] = df["high"].rolling(window=20).max()
        df["low_20"] = df["low"].rolling(window=20).min()

        return df

    def calculate_intraday_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """イントラデイ用のテクニカル指標を計算する"""
        df = df.copy()

        # 短期移動平均（分足用）
        df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
        df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()

        # VWAP
        df["vwap"] = self._calculate_vwap(df)

        # RSI（短期）
        df["rsi"] = self._calculate_rsi(df["close"], period=9)

        # 出来高分析
        df["volume_ma20"] = df["volume"].rolling(window=20).mean()
        df["volume_ratio"] = df["volume"] / df["volume_ma20"]

        # ATR（短期）
        df["atr"] = self._calculate_atr(df, period=14)

        return df

    # ----------------------------------------------------------
    # プライベートメソッド
    # ----------------------------------------------------------

    def _calculate_vwap(self, df: pd.DataFrame) -> pd.Series:
        """VWAP（出来高加重平均価格）を計算する"""
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        return (typical_price * df["volume"]).cumsum() / df["volume"].cumsum()

    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        """RSI（相対力指数）を計算する"""
        delta = prices.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, float("inf"))
        return 100 - (100 / (1 + rs))

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """ATR（Average True Range）を計算する"""
        high = df["high"]
        low = df["low"]
        prev_close = df["close"].shift(1)

        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)

        return tr.ewm(span=period, adjust=False).mean()

    def _get_trend(self, symbol: str) -> str:
        """銘柄のトレンド方向を判断する（up/down/neutral）"""
        df = self.get_historical_data(symbol, period="3mo", interval="1d")
        if df is None or len(df) < 50:
            return "neutral"

        df = self.calculate_indicators(df)
        last = df.iloc[-1]

        if last["close"] > last["ema20"] > last["ema50"]:
            return "up"
        elif last["close"] < last["ema20"] < last["ema50"]:
            return "down"
        return "neutral"

    def _classify_market_condition(self, vix: Optional[float], trend: str) -> str:
        """市場環境を分類する"""
        if vix is None:
            return "unknown"
        if vix > 35:
            return "extreme_fear"  # 取引停止
        elif vix > 25:
            return "fear"          # 取引量半減
        elif vix > 18:
            return "neutral"
        else:
            if trend == "up":
                return "bullish"
            elif trend == "down":
                return "bearish"
            return "neutral"

    def _is_cache_valid(self, cache_key: str) -> bool:
        """キャッシュが有効かチェックする"""
        if cache_key not in self.cache:
            return False
        if cache_key not in self.cache_expiry:
            return False
        return datetime.now() < self.cache_expiry[cache_key]

    def get_stock_info(self, symbol: str) -> Dict:
        """銘柄の基本情報を取得する"""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            return {
                "symbol": symbol,
                "name": info.get("longName", symbol),
                "sector": info.get("sector", "N/A"),
                "market_cap": info.get("marketCap", 0),
                "avg_volume": info.get("averageVolume", 0),
                "beta": info.get("beta", 1.0),
                "52w_high": info.get("fiftyTwoWeekHigh", 0),
                "52w_low": info.get("fiftyTwoWeekLow", 0),
            }
        except Exception as e:
            logger.error(f"{symbol} 銘柄情報取得エラー: {e}")
            return {"symbol": symbol}
