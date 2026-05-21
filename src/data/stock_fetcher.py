"""
株価・財務データ取得モジュール
================================
日本株（東証）・米国株（NASDAQ/NYSE）の株価・財務データを
yfinanceから取得し、pickleキャッシュに保存する。

データ取得可能な情報:
  - 株価履歴（日足・週足）
  - 基本財務指標（PER・PBR・ROE・配当利回り等）
  - 決算カレンダー（次回決算日）
  - 財務諸表（年次・四半期）

制約・限界:
  - yfinanceは非公式ライブラリ（Yahooの仕様変更で突然使えなくなる可能性あり）
  - 日本株の財務データはUS株より項目数が少ない場合あり
  - 決算日は概算であり、正確な日程はIRで要確認
  - リアルタイム株価は15分遅延
"""
from __future__ import annotations

import pickle
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import pytz
import yfinance as yf
from loguru import logger

from config.universe import get_symbol_info, is_japan_stock, to_yfinance_symbol

JST = pytz.timezone("Asia/Tokyo")
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "recommend_cache"


class StockFetcher:
    """
    日本株・米国株 統合データ取得クラス

    キャッシュ戦略:
      株価履歴   : 8時間（営業時間外は変化しないため）
      財務データ : 24時間（日次更新で十分）
      決算情報   : 24時間
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ─── キャッシュ管理 ────────────────────────────────────────────

    def _cache_path(self, key: str) -> Path:
        safe_key = key.replace("/", "_").replace("^", "_").replace("-", "_")
        return self.cache_dir / f"{safe_key}.pkl"

    def _is_fresh(self, path: Path, max_age_hours: float) -> bool:
        if not path.exists():
            return False
        age_sec = datetime.now().timestamp() - path.stat().st_mtime
        return age_sec < max_age_hours * 3600

    def _save(self, path: Path, data: Any) -> None:
        with open(path, "wb") as f:
            pickle.dump(data, f)

    def _load(self, path: Path) -> Any:
        with open(path, "rb") as f:
            return pickle.load(f)

    # ─── 株価履歴 ──────────────────────────────────────────────────

    def fetch_price_history(
        self,
        symbol: str,
        period: str = "2y",
        interval: str = "1d",
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        株価履歴を取得（日足デフォルト・最大2年分）

        Args:
            symbol  : 銘柄コード（例: "8306" / "AAPL"）
            period  : yfinance period文字列（"1y"/"2y"/"6mo"等）
            interval: "1d"（日足）/ "1wk"（週足）/ "1mo"（月足）
        Returns:
            columns: open, high, low, close, volume
            index  : DatetimeIndex（JSTまたはUTC）
        """
        ticker_sym = to_yfinance_symbol(symbol)
        cache_key = f"price_{symbol}_{period}_{interval}"
        cache_path = self._cache_path(cache_key)

        if use_cache and self._is_fresh(cache_path, max_age_hours=8.0):
            logger.debug(f"[{symbol}] 株価履歴: キャッシュ使用")
            return self._load(cache_path)

        logger.info(f"[{symbol}] 株価履歴取得中 ({period}, {interval})...")
        try:
            ticker = yf.Ticker(ticker_sym)
            df = ticker.history(period=period, interval=interval)
        except Exception as e:
            logger.error(f"[{symbol}] 株価取得失敗: {e}")
            return pd.DataFrame()

        if df.empty:
            logger.warning(f"[{symbol}] データなし")
            return pd.DataFrame()

        df = self._clean_price_df(df)
        self._save(cache_path, df)
        logger.info(
            f"[{symbol}] 取得完了: {len(df)}行 "
            f"({df.index.min().date()} 〜 {df.index.max().date()})"
        )
        return df

    def _clean_price_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """株価DataFrameの列名統一・クリーニング"""
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        # 必要列のみ抽出（dividends/stock splitsは除外）
        keep_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[keep_cols]

        # 出来高ゼロ・価格ゼロの行を除去
        if "volume" in df.columns:
            df = df[df["volume"] > 0]
        if "close" in df.columns:
            df = df[df["close"] > 0]

        # 重複インデックス除去
        df = df[~df.index.duplicated(keep="first")].sort_index()
        df.index.name = "date"
        return df

    def fetch_universe_prices(
        self,
        symbols: list[str],
        period: str = "2y",
        use_cache: bool = True,
        max_workers: int = 10,
    ) -> dict[str, pd.DataFrame]:
        """複数銘柄の株価履歴を並列取得"""
        result = {}

        def _fetch(sym):
            return sym, self.fetch_price_history(sym, period=period, use_cache=use_cache)

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_fetch, s): s for s in symbols}
            for future in as_completed(futures):
                try:
                    sym, df = future.result()
                    if not df.empty:
                        result[sym] = df
                except Exception as e:
                    logger.error(f"株価取得エラー: {e}")

        logger.info(f"株価一括取得完了: {len(result)}/{len(symbols)} 銘柄")
        return result

    # ─── 財務データ ────────────────────────────────────────────────

    def fetch_fundamentals(self, symbol: str, use_cache: bool = True) -> dict:
        """
        基本財務指標を取得

        返り値のキー:
            symbol, name, market
            current_price, market_cap
            per_trailing, per_forward, pbr, roe, roa
            eps_ttm, bps, dividend_yield
            revenue_growth, earnings_growth
            debt_to_equity, current_ratio
            free_cashflow
            next_earnings_date
            currency
            [data_quality]: "full" / "partial" / "unavailable"
        """
        cache_key = f"fundamentals_{symbol}"
        cache_path = self._cache_path(cache_key)

        if use_cache and self._is_fresh(cache_path, max_age_hours=24.0):
            logger.debug(f"[{symbol}] 財務データ: キャッシュ使用")
            return self._load(cache_path)

        logger.info(f"[{symbol}] 財務データ取得中...")
        ticker_sym = to_yfinance_symbol(symbol)
        try:
            ticker = yf.Ticker(ticker_sym)
            info = ticker.info or {}
        except Exception as e:
            logger.error(f"[{symbol}] 財務データ取得失敗: {e}")
            return {"symbol": symbol, "data_quality": "unavailable"}

        fundamentals = self._parse_info(symbol, info)

        # バランスシート（Altman Z-Score用: totalAssets / workingCapital / retainedEarnings）
        try:
            bs = ticker.quarterly_balance_sheet
            if bs is not None and not bs.empty:
                latest = bs.iloc[:, 0]  # 最新四半期
                def _bs(key):
                    return float(latest[key]) if key in latest.index and latest[key] is not None else None
                fundamentals["total_assets"]      = _bs("Total Assets")
                # workingCapital = CurrentAssets - CurrentLiabilities
                cur_a = _bs("Current Assets")
                cur_l = _bs("Current Liabilities")
                if cur_a is not None and cur_l is not None:
                    fundamentals["working_capital"] = cur_a - cur_l
                fundamentals["retained_earnings"] = _bs("Retained Earnings")
        except Exception:
            pass

        # 決算カレンダー（next earnings date）
        try:
            calendar = ticker.calendar
            if calendar is not None and not calendar.empty:
                # calendar は DatetimeIndex の DataFrame
                if hasattr(calendar, 'iloc'):
                    # 新形式: Dict[str, list]
                    pass
                next_date = self._extract_next_earnings(calendar)
                fundamentals["next_earnings_date"] = next_date
        except Exception:
            fundamentals["next_earnings_date"] = None

        self._save(cache_path, fundamentals)
        return fundamentals

    def _parse_info(self, symbol: str, info: dict) -> dict:
        """yfinance info dictから財務指標を抽出・正規化"""
        base_info = get_symbol_info(symbol)

        def safe_get(key: str, default=None):
            v = info.get(key, default)
            return v if v is not None and v != "Infinity" else default

        result: dict[str, Any] = {
            "symbol": symbol,
            "name": base_info.get("name") or safe_get("longName") or safe_get("shortName") or symbol,
            "sector": base_info.get("sector") or safe_get("sector"),
            "market": base_info.get("market"),
            "currency": safe_get("currency", "JPY" if is_japan_stock(symbol) else "USD"),

            # 株価
            "current_price": safe_get("currentPrice") or safe_get("regularMarketPrice"),
            "market_cap": safe_get("marketCap"),

            # バリュエーション
            "per_trailing": safe_get("trailingPE"),
            "per_forward":  safe_get("forwardPE"),
            "pbr":          safe_get("priceToBook"),
            "psr":          safe_get("priceToSalesTrailing12Months"),

            # 収益性
            "roe":          safe_get("returnOnEquity"),      # 小数（0.12 = 12%）
            "roa":          safe_get("returnOnAssets"),
            "operating_margin": safe_get("operatingMargins"),
            "profit_margin":    safe_get("profitMargins"),

            # 一株指標
            "eps_ttm":      safe_get("trailingEps"),
            "eps_forward":  safe_get("forwardEps"),
            "bps":          safe_get("bookValue"),           # Book Value Per Share

            # 配当
            # yfinanceは日本株の dividendYield を小数（0.03）ではなく%（3.0）で返す場合がある
            # 0.20超（20%超）は明らかに異常値なので100で割って正規化する
            "dividend_yield": (
                (lambda v: v / 100 if v and v > 0.20 else v)(safe_get("dividendYield"))
            ),
            "dividend_rate":   safe_get("dividendRate"),

            # 成長性
            "revenue_growth":   safe_get("revenueGrowth"),   # 小数（0.15 = 15%）
            "earnings_growth":  safe_get("earningsGrowth"),
            "earnings_quarterly_growth": safe_get("earningsQuarterlyGrowth"),

            # 財務健全性
            "debt_to_equity": safe_get("debtToEquity"),
            "current_ratio":  safe_get("currentRatio"),
            "quick_ratio":    safe_get("quickRatio"),

            # キャッシュフロー
            "free_cashflow":  safe_get("freeCashflow"),
            "operating_cashflow": safe_get("operatingCashflow"),

            # 52週高値・安値
            "week52_high": safe_get("fiftyTwoWeekHigh"),
            "week52_low":  safe_get("fiftyTwoWeekLow"),

            # アナリスト目標株価
            "target_mean_price":   safe_get("targetMeanPrice"),
            "target_median_price": safe_get("targetMedianPrice"),
            "recommendation":      safe_get("recommendationKey"),
            "analyst_count":       safe_get("numberOfAnalystOpinions"),

            # ── 追加スクリーニング材料 ──────────────────────────────
            # PEGレシオ（成長調整後バリュエーション）
            "peg_ratio":      safe_get("pegRatio") or safe_get("trailingPegRatio"),

            # EV/EBITDA（負債構造を考慮した割安度）
            "ev_ebitda":      safe_get("enterpriseToEbitda"),
            "enterprise_value": safe_get("enterpriseValue"),
            "ebitda":         safe_get("ebitda"),

            # ベータ（市場感応度）
            "beta":           safe_get("beta"),

            # Altman Z-Score 計算用（infoで取れるもの）
            "total_debt_abs": safe_get("totalDebt"),
            "total_revenue":  safe_get("totalRevenue"),

            "next_earnings_date": None,
        }

        # データ品質評価
        key_fields = ["per_trailing", "pbr", "roe", "eps_ttm"]
        available = sum(1 for k in key_fields if result.get(k) is not None)
        if available >= 3:
            result["data_quality"] = "full"
        elif available >= 1:
            result["data_quality"] = "partial"
        else:
            result["data_quality"] = "unavailable"

        return result

    def _extract_next_earnings(self, calendar) -> Optional[str]:
        """決算カレンダーから次回決算日を抽出"""
        try:
            if isinstance(calendar, dict):
                date_val = calendar.get("Earnings Date", [None])[0]
                if date_val:
                    return str(date_val)[:10]
            elif hasattr(calendar, "T"):
                # DataFrame形式
                if "Earnings Date" in calendar.T.columns:
                    date_val = calendar.T["Earnings Date"].iloc[0]
                    return str(date_val)[:10]
        except Exception:
            pass
        return None

    def fetch_universe_fundamentals(
        self,
        symbols: list[str],
        use_cache: bool = True,
        max_workers: int = 10,
    ) -> dict[str, dict]:
        """複数銘柄の財務データを並列取得"""
        result = {}

        def _fetch(sym):
            return sym, self.fetch_fundamentals(sym, use_cache=use_cache)

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_fetch, s): s for s in symbols}
            for future in as_completed(futures):
                try:
                    sym, data = future.result()
                    if data.get("data_quality") != "unavailable":
                        result[sym] = data
                    else:
                        logger.warning(f"[{sym}] 財務データ取得不可 → スキップ")
                except Exception as e:
                    logger.error(f"財務データ取得エラー: {e}")

        logger.info(f"財務データ一括取得完了: {len(result)}/{len(symbols)} 銘柄")
        return result
