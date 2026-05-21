"""
ユニバース管理モジュール
========================
取引対象銘柄プールの定義・データ取得・事前フィルタリング

設計思想:
  - 固定リストではなく「条件を満たす銘柄を自動選別」
  - 全データはyfinance（無料）で取得 → 外部有料APIに依存しない
  - フィルター条件: ¥500K資金で1ロット取引可能 + 日次出来高100万株超

ユニバース更新頻度: 月次で手動確認推奨（株価・流動性が変動するため）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf
from loguru import logger

# ─── 候補銘柄マスタ（セクター分散・流動性考慮済み）────────────────
# 選定基準: 日次出来高100万株超、事業内容が分かりやすい代表銘柄
CANDIDATE_UNIVERSE: Dict[str, str] = {
    # 金融（高流動性・景気敏感）
    "8306": "三菱UFJ",
    "8316": "三井住友FG",
    "8411": "みずほFG",
    "8766": "東京海上HD",
    # 自動車（ドル円感応度高・出来高豊富）
    "7203": "トヨタ",
    "7267": "ホンダ",
    "7269": "スズキ",
    "7270": "SUBARU",
    # 電機・精密
    "6758": "ソニーG",
    "6503": "三菱電機",
    "6702": "富士通",
    "6301": "コマツ",
    # 通信・IT（ディフェンシブ）
    "9432": "NTT",
    "9433": "KDDI",
    "9984": "ソフトバンクG",
    # 商社（資源価格連動）
    "8031": "三井物産",
    "8058": "三菱商事",
    "8001": "伊藤忠",
    # 化学・素材
    "4063": "信越化学",
    "4188": "三菱ケミカル",
    "4502": "武田薬品",
    # 半導体・電子部品（グローバル需要連動）
    "6723": "ルネサス",
    "6981": "村田製作所",
    # 内需・小売
    "3382": "セブン&アイ",
    "8267": "イオン",
    "2914": "JT",
    # 素材・エネルギー
    "5401": "日本製鉄",
    "5020": "ENEOS",
    "1801": "大成建設",
}

# スクリーニングフィルター定数
MIN_DAILY_VOLUME = 1_000_000     # 日次出来高最低100万株
MAX_MARGIN_REQUIRED = 450_000    # 1ロット必要証拠金上限（¥500K資金の90%）
MARGIN_RATE = 0.30               # 信用取引委託保証金率（最低30%）
LOT_SIZE = 100                   # 単元株数


class UniverseManager:
    """
    取引対象銘柄の管理クラス

    責務:
    1. 候補マスタから実際に取引可能な銘柄を動的フィルタリング
    2. 各銘柄の日足データを一括取得・キャッシュ
    3. スクリーニング用の基礎統計を提供
    """

    def __init__(self, capital: float = 500_000, cache_dir: Optional[str] = None):
        self.capital = capital
        if cache_dir is None:
            base = Path(__file__).resolve().parents[2]
            cache_dir = str(base / "data" / "screening_cache")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_tradeable_universe(self, verbose: bool = False) -> Dict[str, str]:
        """
        現在の株価・流動性で実際に取引可能な銘柄を返す

        フィルター:
          - 1ロット必要証拠金 ≤ capital × 90%
          - 日次出来高 ≥ 100万株
        """
        tickers = [f"{c}.T" for c in CANDIDATE_UNIVERSE]
        try:
            data = yf.download(tickers, period="5d", interval="1d", progress=False)
        except Exception as e:
            logger.error(f"ユニバースデータ取得失敗: {e}")
            return {}

        closes = data["Close"].iloc[-1]
        volumes = data["Volume"].iloc[-1]

        tradeable = {}
        filtered_out = {}

        for code, name in CANDIDATE_UNIVERSE.items():
            ticker = f"{code}.T"
            if ticker not in closes.index or pd.isna(closes[ticker]):
                filtered_out[code] = "データなし"
                continue

            price = closes[ticker]
            vol = volumes.get(ticker, 0)
            lot_value = price * LOT_SIZE
            margin_required = lot_value * MARGIN_RATE

            if margin_required > MAX_MARGIN_REQUIRED:
                filtered_out[code] = f"証拠金不足(¥{margin_required:,.0f})"
                continue
            if vol < MIN_DAILY_VOLUME:
                filtered_out[code] = f"流動性不足({vol:,.0f}株)"
                continue

            tradeable[code] = name

        if verbose:
            logger.info(f"取引可能銘柄: {len(tradeable)}銘柄 / 候補{len(CANDIDATE_UNIVERSE)}銘柄")
            for code, reason in filtered_out.items():
                logger.debug(f"  除外[{code}]: {reason}")

        return tradeable

    def fetch_daily_data(
        self,
        codes: List[str],
        period: str = "3mo",
        use_cache: bool = True,
    ) -> Dict[str, pd.DataFrame]:
        """
        銘柄リストの日足データを一括取得

        Args:
            codes: 銘柄コードリスト
            period: 取得期間（yfinance形式: "3mo", "6mo", "1y" など）
            use_cache: キャッシュを使用するか（1日1回の取得を想定）
        """
        cache_path = self.cache_dir / "daily_data.pkl"

        if use_cache and cache_path.exists():
            import pickle
            age_hours = (pd.Timestamp.now().timestamp() - cache_path.stat().st_mtime) / 3600
            if age_hours < 8:  # 8時間以内のキャッシュを使用
                with open(cache_path, "rb") as f:
                    cached = pickle.load(f)
                # キャッシュにある銘柄のみ返す
                result = {c: df for c, df in cached.items() if c in codes}
                if len(result) == len(codes):
                    logger.debug("日足データ: キャッシュから読み込み")
                    return result

        tickers = [f"{c}.T" for c in codes]
        logger.info(f"日足データ取得中: {len(codes)}銘柄 / 期間{period}")

        try:
            raw = yf.download(tickers, period=period, interval="1d", progress=False)
        except Exception as e:
            logger.error(f"日足データ取得失敗: {e}")
            return {}

        result = {}
        for code in codes:
            ticker = f"{code}.T"
            try:
                df = pd.DataFrame({
                    "open": raw["Open"][ticker],
                    "high": raw["High"][ticker],
                    "low": raw["Low"][ticker],
                    "close": raw["Close"][ticker],
                    "volume": raw["Volume"][ticker],
                }).dropna()
                if len(df) >= 20:
                    result[code] = df
            except (KeyError, TypeError):
                logger.warning(f"[{code}] データ抽出失敗")

        # キャッシュ保存
        import pickle
        with open(cache_path, "wb") as f:
            pickle.dump(result, f)
        logger.info(f"日足データ取得完了: {len(result)}銘柄")
        return result
