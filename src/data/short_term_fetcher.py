"""
短期モメンタムスクリーニング用データ取得モジュール
==================================================
yfinance から PEAD（決算後モメンタム）・テクニカル・需給データを取得する。

取得データ:
  - 決算日・EPS beat幅・決算日出来高（PEAD判定用）
  - RSI(14)・200日MA・52週高値乖離率・出来高比（テクニカル）
  - 空売り残高比率（ショートスクイーズ判定用）

キャッシュTTL: 24時間（短期シグナルの鮮度重視）
"""
from __future__ import annotations

import pickle
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf
from loguru import logger

from config.universe import to_yfinance_symbol

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "recommend_cache"
CACHE_TTL_HOURS = 24.0


# ─── データクラス ─────────────────────────────────────────────────────

@dataclass
class ShortTermRawData:
    """短期スクリーニング用の生データ"""

    symbol: str
    name: str
    current_price: Optional[float] = None

    # ── PEAD（Post-Earnings Announcement Drift）
    last_earnings_date: Optional[str] = None       # "2024-01-15"
    days_since_earnings: Optional[int] = None
    eps_beat_pct: Optional[float] = None            # (実績 - 予想) / |予想|（小数）
    earnings_day_volume_ratio: Optional[float] = None  # 決算日出来高 / 60日平均出来高

    # ── テクニカル
    rsi_14: Optional[float] = None
    above_ma200: Optional[bool] = None
    pct_from_52w_high: Optional[float] = None       # (現在値 - 52週高値) / 52週高値（負値）
    volume_ratio_10d: Optional[float] = None        # 直近出来高 / 10日平均出来高

    # ── 需給
    short_percent_of_float: Optional[float] = None  # 小数（0.15 = 15%）

    # ── 参考値
    week52_high: Optional[float] = None
    week52_low: Optional[float] = None


# ─── フェッチャー ─────────────────────────────────────────────────────

class ShortTermFetcher:
    """短期スクリーニング用データを取得するクラス"""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ── キャッシュ管理 ──────────────────────────────────────────────

    def _cache_path(self, symbol: str) -> Path:
        safe = symbol.replace("/", "_").replace("^", "_").replace("-", "_")
        return self.cache_dir / f"shortterm_{safe}.pkl"

    def _is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        age_sec = datetime.now().timestamp() - path.stat().st_mtime
        return age_sec < CACHE_TTL_HOURS * 3600

    # ── テクニカル計算 ──────────────────────────────────────────────

    @staticmethod
    def _calc_rsi(prices: pd.Series, period: int = 14) -> Optional[float]:
        """RSI(period)を計算する"""
        if len(prices) < period + 1:
            return None
        delta = prices.diff().dropna()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / loss.replace(0, float("nan"))
        rsi   = 100 - 100 / (1 + rs)
        val   = rsi.iloc[-1]
        return float(val) if pd.notna(val) else None

    # ── 1銘柄取得 ───────────────────────────────────────────────────

    def fetch(
        self,
        symbol: str,
        fd: dict,
        use_cache: bool = True,
    ) -> ShortTermRawData:
        """1銘柄の短期スクリーニング用データを取得する"""
        cache_path = self._cache_path(symbol)

        if use_cache and self._is_fresh(cache_path):
            logger.debug(f"[{symbol}] 短期データ: キャッシュ使用")
            with open(cache_path, "rb") as f:
                return pickle.load(f)

        logger.info(f"[{symbol}] 短期データ取得中...")

        name          = fd.get("name") or symbol
        current_price = fd.get("current_price")
        week52_high   = fd.get("week52_high")
        week52_low    = fd.get("week52_low")

        ticker_sym = to_yfinance_symbol(symbol)
        ticker     = yf.Ticker(ticker_sym)
        info       = ticker.info or {}

        if current_price is None:
            current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        if week52_high is None:
            week52_high = info.get("fiftyTwoWeekHigh")
        if week52_low is None:
            week52_low = info.get("fiftyTwoWeekLow")

        # ── 価格履歴から各テクニカル指標を計算 ──────────────────────
        rsi_14 = above_ma200 = pct_from_52w_high = volume_ratio_10d = None

        try:
            hist = ticker.history(period="1y")
            if hist is not None and len(hist) >= 20:
                close  = hist["Close"].dropna()
                volume = hist["Volume"].dropna()

                rsi_14 = self._calc_rsi(close)

                # 200日MA（データが200日に満たない場合は全期間平均）
                if len(close) >= 200:
                    ma200 = close.rolling(200).mean().iloc[-1]
                else:
                    ma200 = close.mean()
                if pd.notna(ma200) and current_price is not None:
                    above_ma200 = float(current_price) > float(ma200)

                # 52週高値乖離率
                if week52_high and current_price:
                    pct_from_52w_high = (float(current_price) - float(week52_high)) / float(week52_high)

                # 直近出来高 / 10日平均
                if len(volume) >= 11:
                    avg_vol_10d = float(volume.iloc[-11:-1].mean())
                    latest_vol  = float(volume.iloc[-1])
                    if avg_vol_10d > 0:
                        volume_ratio_10d = latest_vol / avg_vol_10d

        except Exception as e:
            logger.debug(f"[{symbol}] 価格履歴取得失敗: {e}")

        # ── PEAD: 決算日・EPS beat・決算日出来高 ──────────────────────
        last_earnings_date = None
        days_since_earnings = None
        eps_beat_pct = None
        earnings_day_volume_ratio = None

        try:
            ed = ticker.earnings_dates
            if ed is not None and not ed.empty:
                today = pd.Timestamp.now(tz="UTC")

                # 過去の決算のみ対象（予想日を除外）
                past = ed[ed.index <= today]
                past = past.dropna(subset=["EPS Estimate", "Reported EPS"], how="all")

                if not past.empty:
                    latest = past.iloc[0]
                    earn_ts = latest.name

                    last_earnings_date  = str(earn_ts.date())
                    days_since_earnings = (today.date() - earn_ts.date()).days

                    est = latest.get("EPS Estimate")
                    act = latest.get("Reported EPS")

                    if est is not None and act is not None:
                        try:
                            est_f = float(est)
                            act_f = float(act)
                            if est_f != 0:
                                eps_beat_pct = (act_f - est_f) / abs(est_f)
                        except (ValueError, TypeError):
                            pass

                    # 決算日の出来高比（60日平均比）
                    try:
                        hist_vol = ticker.history(period="1y")["Volume"].dropna()
                        avg_vol  = float(hist_vol.iloc[-60:].mean()) if len(hist_vol) >= 10 else None
                        earn_date = earn_ts.date()

                        # 日付でフィルタ
                        hist_full = ticker.history(period="1y")
                        earn_mask = pd.Series(hist_full.index).apply(
                            lambda x: x.date() == earn_date
                        ).values
                        earn_row = hist_full[earn_mask]

                        if not earn_row.empty and avg_vol and avg_vol > 0:
                            earn_vol = float(earn_row["Volume"].iloc[0])
                            earnings_day_volume_ratio = earn_vol / avg_vol
                    except Exception as e:
                        logger.debug(f"[{symbol}] 決算日出来高計算失敗: {e}")

        except Exception as e:
            logger.debug(f"[{symbol}] 決算データ取得失敗: {e}")

        # ── 空売り残高 ────────────────────────────────────────────────
        short_percent_of_float = None
        try:
            sp = info.get("shortPercentOfFloat")
            if sp is not None:
                short_percent_of_float = float(sp)
        except Exception:
            pass

        data = ShortTermRawData(
            symbol=symbol,
            name=name,
            current_price=current_price,
            last_earnings_date=last_earnings_date,
            days_since_earnings=days_since_earnings,
            eps_beat_pct=eps_beat_pct,
            earnings_day_volume_ratio=earnings_day_volume_ratio,
            rsi_14=rsi_14,
            above_ma200=above_ma200,
            pct_from_52w_high=pct_from_52w_high,
            volume_ratio_10d=volume_ratio_10d,
            short_percent_of_float=short_percent_of_float,
            week52_high=week52_high,
            week52_low=week52_low,
        )

        with open(cache_path, "wb") as f:
            pickle.dump(data, f)

        logger.info(f"[{symbol}] 短期データ取得完了")
        return data

    # ── 複数銘柄並列取得 ────────────────────────────────────────────

    def fetch_universe(
        self,
        symbols: list[str],
        fd_dict: dict[str, dict],
        use_cache: bool = True,
        max_workers: int = 8,
    ) -> dict[str, ShortTermRawData]:
        """複数銘柄を並列取得する"""
        result: dict[str, ShortTermRawData] = {}

        def _fetch_one(sym: str):
            fd = fd_dict.get(sym, {})
            return sym, self.fetch(sym, fd, use_cache=use_cache)

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_fetch_one, s): s for s in symbols}
            for future in as_completed(futures):
                sym = futures[future]
                try:
                    sym_out, data = future.result()
                    result[sym_out] = data
                except Exception as e:
                    logger.debug(f"[{sym}] 短期データ取得失敗（スキップ）: {e}")

        logger.info(f"短期データ一括取得完了: {len(result)}/{len(symbols)}銘柄")
        return result
