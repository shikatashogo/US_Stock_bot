"""
SEC EDGAR インサイダー取引データ取得モジュール
==============================================
Form 4（役員・大株主の持株変動報告書）を取得・解析し、
インサイダーの売買動向をスコアリングする。

対象: 米国株のみ（日本株はEDINETが別途必要）
料金: 完全無料（SEC EDGAR公開API）
制限: 10リクエスト/秒以内（SECのガイドライン）

インサイダー取引の読み方:
  P (Purchase)  = 市場での買い  → 強気サイン
  S (Sale)      = 市場での売り  → 弱気サイン
  A (Award)     = ストック付与  → 中立（報酬）
  F (Tax)       = 税支払い用売却 → 中立
  G (Gift)      = 贈与         → 中立
"""
from __future__ import annotations

import pickle
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from loguru import logger

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "recommend_cache"

# EDGAR API エンドポイント
_TICKER_MAP_URL  = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_ARCHIVE_URL     = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}"

# SEC のガイドライン: User-Agent 必須
_HEADERS = {
    "User-Agent": "US_Stock_Bot contact@example.com",
    "Accept-Encoding": "gzip, deflate",
}

# 意味のある取引タイプのみ集計（報酬・税支払いは除外）
_BUY_TYPES  = {"P"}          # 市場での買い
_SELL_TYPES = {"S"}          # 市場での売り
_NEUTRAL_TYPES = {"A", "F", "G", "D", "M", "X"}  # 報酬・税・贈与等


class EdgarFetcher:
    """SEC EDGAR から Form 4（インサイダー取引）を取得するクラス"""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._ticker_map: dict = {}  # ticker → CIK のグローバルキャッシュ

    # ─── キャッシュ管理 ────────────────────────────────────────────

    def _cache_path(self, key: str) -> Path:
        safe = key.replace("/", "_").replace("^", "_")
        return self.cache_dir / f"edgar_{safe}.pkl"

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

    # ─── CIK ルックアップ ──────────────────────────────────────────

    def _load_ticker_map(self) -> dict:
        """ticker → CIK のマッピングを取得（グローバルキャッシュ）"""
        cache = self._cache_path("ticker_map")
        if self._is_fresh(cache, max_age_days=7):
            return self._load(cache)

        try:
            resp = requests.get(_TICKER_MAP_URL, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            raw = resp.json()
            # {0: {cik_str: "0000320193", ticker: "AAPL", title: "..."}, ...}
            mapping = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in raw.values()}
            self._save(cache, mapping)
            logger.debug("EDGAR tickerマップ取得完了")
            return mapping
        except Exception as e:
            logger.warning(f"EDGAR tickerマップ取得失敗: {e}")
            return {}

    def _get_cik(self, ticker: str) -> Optional[str]:
        """ティッカーから CIK（10桁ゼロ埋め）を返す"""
        if not self._ticker_map:
            self._ticker_map = self._load_ticker_map()
        return self._ticker_map.get(ticker.upper())

    # ─── Form 4 取得・解析 ─────────────────────────────────────────

    def _get_submissions(self, cik: str) -> dict:
        """CIK の filings一覧を取得"""
        cache = self._cache_path(f"submissions_{cik}")
        if self._is_fresh(cache, max_age_days=1):
            return self._load(cache)

        url = _SUBMISSIONS_URL.format(cik=cik)
        try:
            time.sleep(0.12)  # SEC レート制限対応（max 10req/sec）
            resp = requests.get(url, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            self._save(cache, data)
            return data
        except Exception as e:
            logger.warning(f"EDGAR submissions取得失敗 CIK={cik}: {e}")
            return {}

    def _get_recent_form4(self, submissions: dict, days: int = 180) -> list[dict]:
        """直近N日以内の Form 4 filing一覧を返す"""
        filings = submissions.get("filings", {}).get("recent", {})
        forms    = filings.get("form", [])
        dates    = filings.get("filingDate", [])
        accnums  = filings.get("accessionNumber", [])
        prim_docs= filings.get("primaryDocument", [])

        cutoff = datetime.now() - timedelta(days=days)
        result = []
        for form, date_str, acc, doc in zip(forms, dates, accnums, prim_docs):
            if form != "4":
                continue
            try:
                filing_date = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue
            if filing_date < cutoff:
                break  # 日付降順のため以降は不要
            result.append({
                "date": date_str,
                "accessionNumber": acc,
                "primaryDocument": doc,
            })
        return result[:10]  # 最大10件に制限

    def _parse_form4_xml(self, cik: str, accession: str, doc: str) -> dict:
        """Form 4 XML を解析して買い/売り株数を集計"""
        acc_nodash = accession.replace("-", "")
        url = _ARCHIVE_URL.format(cik=int(cik), accession=acc_nodash, doc=doc)

        try:
            time.sleep(0.12)
            resp = requests.get(url, headers=_HEADERS, timeout=10)
            resp.raise_for_status()
        except Exception:
            return {"buy": 0, "sell": 0}

        # XML でない場合（HTML index など）はスキップ
        content_type = resp.headers.get("Content-Type", "")
        if "xml" not in content_type and not doc.endswith(".xml"):
            return {"buy": 0, "sell": 0}

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            return {"buy": 0, "sell": 0}

        buy_shares  = 0.0
        sell_shares = 0.0

        # nonDerivativeTransaction（現物株）のみ対象
        for txn in root.iter("nonDerivativeTransaction"):
            txn_type_el = txn.find(".//transactionType")
            shares_el   = txn.find(".//transactionShares/value")
            if txn_type_el is None or shares_el is None:
                continue
            txn_type = txn_type_el.text or ""
            try:
                shares = float(shares_el.text or 0)
            except ValueError:
                continue

            if txn_type in _BUY_TYPES:
                buy_shares  += shares
            elif txn_type in _SELL_TYPES:
                sell_shares += shares
            # 中立タイプ（A/F/G等）は集計しない

        return {"buy": buy_shares, "sell": sell_shares}

    # ─── メイン公開 API ────────────────────────────────────────────

    def fetch_insider_transactions(
        self,
        ticker: str,
        days: int = 180,
        use_cache: bool = True,
    ) -> dict:
        """
        直近N日のインサイダー売買を集計して返す

        Returns:
            {
              "insider_sentiment":   "買い越し" / "売り越し" / "中立" / None,
              "insider_buy_shares":  float,
              "insider_sell_shares": float,
              "insider_net_shares":  float,   # 正=買い越し、負=売り越し
              "insider_form4_count": int,     # 集計した Form 4 件数
            }
        """
        cache = self._cache_path(f"insider_{ticker}")
        if use_cache and self._is_fresh(cache, max_age_days=7):
            return self._load(cache)

        empty = {
            "insider_sentiment":   None,
            "insider_buy_shares":  0.0,
            "insider_sell_shares": 0.0,
            "insider_net_shares":  0.0,
            "insider_form4_count": 0,
        }

        cik = self._get_cik(ticker)
        if not cik:
            logger.debug(f"[{ticker}] EDGAR CIK未発見（日本株または未登録）")
            return empty

        submissions = self._get_submissions(cik)
        if not submissions:
            return empty

        form4_list = self._get_recent_form4(submissions, days=days)
        if not form4_list:
            logger.debug(f"[{ticker}] 直近{days}日のForm 4なし")
            result = {**empty, "insider_form4_count": 0}
            self._save(cache, result)
            return result

        total_buy  = 0.0
        total_sell = 0.0

        for filing in form4_list:
            txn = self._parse_form4_xml(cik, filing["accessionNumber"], filing["primaryDocument"])
            total_buy  += txn["buy"]
            total_sell += txn["sell"]

        net = total_buy - total_sell
        if net > 0:
            sentiment = "買い越し"
        elif net < 0:
            sentiment = "売り越し"
        else:
            sentiment = "中立"

        result = {
            "insider_sentiment":   sentiment,
            "insider_buy_shares":  total_buy,
            "insider_sell_shares": total_sell,
            "insider_net_shares":  net,
            "insider_form4_count": len(form4_list),
        }
        self._save(cache, result)
        logger.info(
            f"[{ticker}] インサイダー: {sentiment} "
            f"（買い{total_buy:,.0f}株 / 売り{total_sell:,.0f}株 / Form4 {len(form4_list)}件）"
        )
        return result

    def fetch_universe_insider(
        self,
        symbols: list[str],
        use_cache: bool = True,
        max_workers: int = 5,  # SEC制限のため少なめ
    ) -> dict[str, dict]:
        """複数銘柄のインサイダーデータを並列取得"""
        result = {}

        # CIKマップを事前ロード（全スレッドで共有）
        self._ticker_map = self._load_ticker_map()

        def _fetch(sym):
            return sym, self.fetch_insider_transactions(sym, use_cache=use_cache)

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_fetch, s): s for s in symbols}
            for future in as_completed(futures):
                try:
                    sym, data = future.result()
                    if data.get("insider_sentiment") is not None:
                        result[sym] = data
                except Exception as e:
                    logger.error(f"インサイダーデータ取得エラー: {e}")

        logger.info(f"インサイダーデータ取得完了: {len(result)}/{len(symbols)} 銘柄")
        return result
