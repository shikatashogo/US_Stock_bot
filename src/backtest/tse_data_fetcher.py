"""
東証（TSE）データ取得モジュール
yfinance を使用して5分足・日足データを取得しキャッシュする

データ制約:
  5分足: 最大60日分（ORBバックテストに使用）
  日足:  最大2年分（ATR・出来高フィルター基準に使用）
  1分足: 最大7日分（本番前の執行精度確認に使用）
"""
import os
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import pytz
import yfinance as yf
from loguru import logger

JST = pytz.timezone("Asia/Tokyo")

# yfinanceでのTSE銘柄コード（.T suffix）
TSE_UNIVERSE = {
    "8306": "三菱UFJフィナンシャル・グループ",
    "6758": "ソニーグループ",
    "7203": "トヨタ自動車",
    "9432": "NTT",
    "9984": "ソフトバンクグループ",
    "6861": "キーエンス",
    "8035": "東京エレクトロン",
    "6954": "ファナック",
    "4063": "信越化学工業",
    "6367": "ダイキン工業",
    "1306": "TOPIX連動ETF（東証）",
}


class TSEDataFetcher:
    """
    東証株価データ取得クラス（キャッシュ付き）

    キャッシュ戦略:
      - 当日のデータは毎回新規取得（マーケット時間中は更新される）
      - 過去データはpickleキャッシュで保存（再取得不要）
    """

    def __init__(self, cache_dir: Optional[str] = None):
        if cache_dir is None:
            base = Path(__file__).resolve().parents[2]
            cache_dir = str(base / "data" / "tse_cache")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _ticker_symbol(self, code: str) -> str:
        """銘柄コードをyfinance形式に変換（例: 8306 → 8306.T）"""
        code = str(code).strip()
        return code if code.endswith(".T") else f"{code}.T"

    def _cache_path(self, code: str, interval: str) -> Path:
        return self.cache_dir / f"{code}_{interval}.pkl"

    def _is_cache_fresh(self, path: Path, max_age_hours: float = 1.0) -> bool:
        """キャッシュが新鮮かどうか判定"""
        if not path.exists():
            return False
        age = datetime.now().timestamp() - path.stat().st_mtime
        return age < max_age_hours * 3600

    def _save_cache(self, path: Path, df: pd.DataFrame) -> None:
        with open(path, "wb") as f:
            pickle.dump(df, f)

    def _load_cache(self, path: Path) -> pd.DataFrame:
        with open(path, "rb") as f:
            return pickle.load(f)

    def fetch_5min(self, code: str, use_cache: bool = True) -> pd.DataFrame:
        """
        5分足データを取得（最大60日分）
        ORBバックテストのメインデータとして使用
        """
        symbol = self._ticker_symbol(code)
        cache_path = self._cache_path(code, "5m")

        if use_cache and self._is_cache_fresh(cache_path, max_age_hours=1.0):
            logger.debug(f"[{code}] 5分足 キャッシュから読み込み")
            return self._load_cache(cache_path)

        logger.info(f"[{code}] 5分足データ取得中（最大60日）...")
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="60d", interval="5m")
        except Exception as e:
            logger.error(f"[{code}] データ取得失敗: {e}")
            return pd.DataFrame()

        if df.empty:
            logger.warning(f"[{code}] 5分足データが空です")
            return pd.DataFrame()

        df = self._clean_df(df, code)
        self._save_cache(cache_path, df)
        logger.info(f"[{code}] 5分足取得完了: {len(df)}行 "
                    f"({df.index.min().date()} 〜 {df.index.max().date()})")
        return df

    def fetch_daily(self, code: str, period: str = "2y", use_cache: bool = True) -> pd.DataFrame:
        """
        日足データを取得（最大2年分）
        ATR・出来高フィルター基準の計算に使用
        """
        symbol = self._ticker_symbol(code)
        cache_path = self._cache_path(code, "1d")

        # 日足は1日1回更新で十分
        if use_cache and self._is_cache_fresh(cache_path, max_age_hours=8.0):
            logger.debug(f"[{code}] 日足 キャッシュから読み込み")
            return self._load_cache(cache_path)

        logger.info(f"[{code}] 日足データ取得中（{period}）...")
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval="1d")
        except Exception as e:
            logger.error(f"[{code}] 日足取得失敗: {e}")
            return pd.DataFrame()

        if df.empty:
            logger.warning(f"[{code}] 日足データが空です")
            return pd.DataFrame()

        df = self._clean_df(df, code)
        self._save_cache(cache_path, df)
        logger.info(f"[{code}] 日足取得完了: {len(df)}行")
        return df

    def _clean_df(self, df: pd.DataFrame, code: str) -> pd.DataFrame:
        """データクリーニング: 列名統一・タイムゾーン確認・異常値除去"""
        # 列名を小文字に統一
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]].copy()

        # タイムゾーンをJSTに統一
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize(JST)
        elif str(df.index.tzinfo) != "Asia/Tokyo":
            df.index = df.index.tz_convert(JST)

        # 出来高ゼロ・価格ゼロの行を除去（板寄せ中の仮データ等）
        df = df[(df["volume"] > 0) & (df["close"] > 0)]

        # 重複インデックス除去
        df = df[~df.index.duplicated(keep="first")]
        df = df.sort_index()

        df.index.name = "datetime"
        return df

    def fetch_universe(
        self,
        codes: Optional[list] = None,
        interval: str = "5m",
        use_cache: bool = True,
    ) -> Dict[str, pd.DataFrame]:
        """
        複数銘柄のデータを一括取得

        Args:
            codes: 銘柄コードリスト（Noneで全ユニバース）
            interval: "5m" or "1d"
        """
        if codes is None:
            codes = list(TSE_UNIVERSE.keys())

        result = {}
        for code in codes:
            try:
                if interval == "5m":
                    df = self.fetch_5min(code, use_cache=use_cache)
                else:
                    df = self.fetch_daily(code, use_cache=use_cache)
                if not df.empty:
                    result[code] = df
            except Exception as e:
                logger.error(f"[{code}] 取得エラー: {e}")

        logger.info(f"ユニバース取得完了: {len(result)}/{len(codes)} 銘柄")
        return result


def calc_atr(daily_df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR（Average True Range）計算"""
    high = daily_df["high"]
    low = daily_df["low"]
    prev_close = daily_df["close"].shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    return tr.ewm(span=period, adjust=False).mean()


def calc_daily_limit(prev_close: float) -> tuple:
    """
    東証の値幅制限を計算する
    参照: https://www.jpx.co.jp/equities/trading/domestic/02.html

    Returns:
        (下限価格, 上限価格)
    """
    limits = [
        (100, 30), (200, 50), (500, 80), (700, 100),
        (1000, 150), (1500, 300), (2000, 400), (3000, 500),
        (5000, 700), (7000, 1000), (10000, 1500), (15000, 3000),
        (20000, 4000), (30000, 5000), (50000, 7000),
        (70000, 10000), (100000, 150000),
    ]
    for threshold, width in limits:
        if prev_close < threshold:
            return prev_close - width, prev_close + width
    # ¥100,000以上: ±30%
    width = prev_close * 0.30
    return prev_close - width, prev_close + width
