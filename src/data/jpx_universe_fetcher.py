"""
JPX上場銘柄一覧取得モジュール
================================
東証（JPX）公式の上場銘柄一覧Excelファイルをダウンロードし、
銘柄コード・市場区分・業種のリストを返す。

データソース:
  https://www.jpx.co.jp/markets/statistics-equities/misc/01.html
  （毎月第3営業日更新）

対象市場:
  - グロース（内国株式）: ~490銘柄
  - スタンダード（内国株式）: ~1570銘柄
  - プライム（内国株式）: ~1570銘柄
"""
from __future__ import annotations

import io
import pickle
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from loguru import logger

JPX_URL = (
    "https://www.jpx.co.jp/markets/statistics-equities/misc/"
    "tvdivq0000001vg2-att/data_j.xls"
)

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "recommend_cache"
CACHE_FILE = CACHE_DIR / "jpx_listing.pkl"
CACHE_TTL_HOURS = 168.0  # 7日間（月次更新なので十分）

# テンバガー向け対象業種（情報通信・サービス・医薬・電機・精密等）
TENBAGGER_SECTORS = {
    "情報・通信業",
    "サービス業",
    "医薬品",
    "電気機器",
    "精密機器",
    "機械",
    "その他製品",
    "化学",
}

# 除外業種（テンバガー向けに不適）
EXCLUDE_SECTORS = {
    "銀行業", "保険業", "証券、商品先物取引業",
    "不動産業", "建設業", "鉱業",
}


class JpxUniverseFetcher:
    """JPX上場銘柄一覧を取得・キャッシュするクラス"""

    def __init__(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _is_fresh(self) -> bool:
        if not CACHE_FILE.exists():
            return False
        age = datetime.now().timestamp() - CACHE_FILE.stat().st_mtime
        return age < CACHE_TTL_HOURS * 3600

    def _save(self, df: pd.DataFrame) -> None:
        with open(CACHE_FILE, "wb") as f:
            pickle.dump(df, f)

    def _load(self) -> pd.DataFrame:
        with open(CACHE_FILE, "rb") as f:
            return pickle.load(f)

    def fetch_listing(self, use_cache: bool = True) -> pd.DataFrame:
        """
        JPX上場銘柄一覧を取得する。

        Returns:
            DataFrame with columns: コード, 銘柄名, 市場・商品区分, 33業種区分
        """
        if use_cache and self._is_fresh():
            logger.debug("JPX銘柄一覧: キャッシュ使用")
            return self._load()

        logger.info("JPX上場銘柄一覧をダウンロード中...")
        try:
            resp = requests.get(
                JPX_URL,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=30,
            )
            resp.raise_for_status()
            df = pd.read_excel(io.BytesIO(resp.content), engine="xlrd")
            self._save(df)
            logger.info(f"JPX銘柄一覧取得完了: {len(df)}件")
            return df
        except Exception as e:
            logger.error(f"JPX銘柄一覧ダウンロード失敗: {e}")
            # キャッシュが古くても使う
            if CACHE_FILE.exists():
                logger.warning("古いキャッシュを使用します")
                return self._load()
            raise

    def _get_symbols(
        self,
        market: str,
        sector_filter: Optional[set[str]] = None,
        exclude_sectors: Optional[set[str]] = None,
        use_cache: bool = True,
    ) -> list[str]:
        """指定市場の銘柄コード一覧を返す"""
        df = self.fetch_listing(use_cache=use_cache)

        # 市場でフィルタ
        mask = df["市場・商品区分"] == market
        filtered = df[mask].copy()

        # 4桁数字コードのみ
        filtered = filtered[
            filtered["コード"].astype(str).str.match(r"^\d{4}$")
        ]

        # 業種フィルタ（include）
        if sector_filter:
            filtered = filtered[
                filtered["33業種区分"].isin(sector_filter)
            ]

        # 業種フィルタ（exclude）
        if exclude_sectors:
            filtered = filtered[
                ~filtered["33業種区分"].isin(exclude_sectors)
            ]

        return filtered["コード"].astype(str).tolist()

    def get_growth_symbols(
        self,
        tenbagger_sector_only: bool = True,
        use_cache: bool = True,
    ) -> list[str]:
        """
        東証グロース市場の銘柄コード一覧を返す。

        Args:
            tenbagger_sector_only: True=テンバガー向け業種のみ（情報通信・サービス等）
                                   False=全業種
        """
        sector_filter = TENBAGGER_SECTORS if tenbagger_sector_only else None
        symbols = self._get_symbols(
            market="グロース（内国株式）",
            sector_filter=sector_filter,
            exclude_sectors=None if tenbagger_sector_only else EXCLUDE_SECTORS,
            use_cache=use_cache,
        )
        logger.info(
            f"東証グロース銘柄: {len(symbols)}件 "
            f"({'テンバガー向け業種' if tenbagger_sector_only else '全業種'})"
        )
        return symbols

    def get_standard_growth_symbols(
        self,
        tenbagger_sector_only: bool = True,
        use_cache: bool = True,
    ) -> list[str]:
        """グロース + スタンダードの成長系銘柄コード一覧"""
        growth = self.get_growth_symbols(tenbagger_sector_only, use_cache)
        standard = self._get_symbols(
            market="スタンダード（内国株式）",
            sector_filter=TENBAGGER_SECTORS if tenbagger_sector_only else None,
            exclude_sectors=EXCLUDE_SECTORS,
            use_cache=use_cache,
        )
        logger.info(
            f"グロース+スタンダード: グロース{len(growth)}件 + スタンダード{len(standard)}件"
        )
        return growth + standard

    def get_listing_summary(self, use_cache: bool = True) -> dict:
        """市場別件数サマリーを返す"""
        df = self.fetch_listing(use_cache=use_cache)
        counts = df["市場・商品区分"].value_counts().to_dict()
        growth_df = df[df["市場・商品区分"] == "グロース（内国株式）"]
        growth_valid = growth_df[
            growth_df["コード"].astype(str).str.match(r"^\d{4}$")
        ]
        growth_tb = growth_valid[
            growth_valid["33業種区分"].isin(TENBAGGER_SECTORS)
        ]
        return {
            "total": len(df),
            "prime": counts.get("プライム（内国株式）", 0),
            "standard": counts.get("スタンダード（内国株式）", 0),
            "growth": counts.get("グロース（内国株式）", 0),
            "growth_valid": len(growth_valid),
            "growth_tenbagger_sector": len(growth_tb),
        }
