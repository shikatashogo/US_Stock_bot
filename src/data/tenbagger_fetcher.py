"""
テンバガー候補データ取得モジュール
====================================
テンバガー（株価10倍以上）スクリーニングに必要な
追加財務データを yfinance から取得し、pickle キャッシュに保存する。

取得データ:
  - 年次キャッシュフロー（FCF: 過去3年）
  - 四半期売上・営業利益（成長加速度・利益率比較）
  - 株式発行数履歴（3年間の希薄化チェック）
  - 浮動株比率
  - ROIC（簡易計算）
"""
from __future__ import annotations

import pickle
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import yfinance as yf
from loguru import logger

from src.data.stock_fetcher import to_yfinance_symbol

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "recommend_cache"
CACHE_TTL_HOURS = 168.0  # 7日間


# ─── テーマ定義 ──────────────────────────────────────────────────────

_THEMES_TAM_LARGE = [
    ("AI",           ["artificial intelligence", "ai ", "machine learning", "deep learning", "llm", "generative",
                      "人工知能", "AI", "機械学習", "ジェネレーティブ"]),
    ("半導体",        ["semiconductor", "chip", "wafer", "fab", "eda",
                      "半導体", "チップ", "ウェハー"]),
    ("サイバーセキュリティ", ["cybersecurity", "security", "endpoint", "firewall", "zero trust",
                      "セキュリティ", "サイバー"]),
    ("データセンター", ["data center", "datacenter", "cloud", "hyperscaler", "colocation",
                      "データセンター", "クラウド"]),
    ("エネルギー転換", ["renewable", "solar", "wind", "battery", "energy storage", "ev", "electric vehicle",
                      "再生可能", "太陽光", "蓄電池", "電気自動車", "EV"]),
    ("FinTech",      ["fintech", "payment", "digital bank", "neobank", "blockchain", "crypto",
                      "フィンテック", "決済", "ブロックチェーン"]),
    ("SaaS",         ["saas", "software as a service", "subscription", "arr", "mrr",
                      "サブスクリプション", "SaaS"]),
]

_THEMES_TAM_SMALL = [
    ("医療DX",       ["health tech", "medtech", "digital health", "telemedicine", "ehrs",
                      "医療DX", "ヘルステック", "遠隔医療"]),
    ("ロボティクス",  ["robotics", "automation", "autonomous", "robot",
                      "ロボット", "自動化", "自律"]),
    ("防衛・宇宙",    ["defense", "aerospace", "satellite", "space", "military",
                      "防衛", "航空宇宙", "衛星", "宇宙"]),
]


def _classify_theme(sector: Optional[str], industry: Optional[str], summary: Optional[str]) -> tuple[str, bool]:
    """セクター・業種・概要からテーマを分類する。
    Returns: (matched_theme, tam_large)
    """
    combined = " ".join(filter(None, [
        (sector or "").lower(),
        (industry or "").lower(),
        (summary or "")[:500].lower(),
    ]))

    for theme_name, keywords in _THEMES_TAM_LARGE:
        if any(kw.lower() in combined for kw in keywords):
            return theme_name, True

    for theme_name, keywords in _THEMES_TAM_SMALL:
        if any(kw.lower() in combined for kw in keywords):
            return theme_name, False

    return "", False


# ─── データクラス ─────────────────────────────────────────────────────

@dataclass
class TenbaggerRawData:
    """テンバガースクリーニング用の生データ"""

    symbol: str
    name: str
    currency: str
    sector: str
    industry: str = ""

    # 時価総額
    market_cap: Optional[float] = None          # ローカル通貨
    market_cap_oku_jpy: Optional[float] = None  # 億円換算

    # 売上成長率（YoY, decimal）
    revenue_growth_current: Optional[float] = None  # 直近Q YoY
    revenue_growth_4q_ago: Optional[float] = None   # 4Q前 YoY

    # 利益率（decimal）
    gross_margin: Optional[float] = None
    op_margin_current: Optional[float] = None
    op_margin_1y_ago: Optional[float] = None

    # FCF（ローカル通貨）
    fcf_current: Optional[float] = None
    fcf_prior: Optional[float] = None
    fcf_2y_prior: Optional[float] = None

    # ROIC（decimal）
    roic: Optional[float] = None

    # 株式数
    shares_current: Optional[float] = None
    shares_3y_ago: Optional[float] = None

    # 浮動株・創業者保有
    float_ratio: Optional[float] = None
    founder_ownership: Optional[float] = None  # 常に None（取得不可）

    # テーマ
    matched_theme: str = ""
    theme_tam_large: bool = False

    # テクニカル（TechnicalSignal から引き継ぎ）
    above_ma200: Optional[bool] = None
    volume_ratio: Optional[float] = None
    pct_from_52w_high: Optional[float] = None

    # データ品質
    data_quality: str = "full"


# ─── フェッチャー ─────────────────────────────────────────────────────

class TenbaggerFetcher:
    """テンバガー候補データ取得クラス"""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ── キャッシュ管理 ──────────────────────────────────────────────

    def _cache_path(self, symbol: str) -> Path:
        safe = symbol.replace("/", "_").replace("^", "_").replace("-", "_")
        return self.cache_dir / f"tenbagger_{safe}.pkl"

    def _is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        age_sec = datetime.now().timestamp() - path.stat().st_mtime
        return age_sec < CACHE_TTL_HOURS * 3600

    def _save(self, path: Path, data: object) -> None:
        with open(path, "wb") as f:
            pickle.dump(data, f)

    def _load(self, path: Path) -> object:
        with open(path, "rb") as f:
            return pickle.load(f)

    # ── 取得ロジック ────────────────────────────────────────────────

    def fetch(
        self,
        symbol: str,
        fd: dict,
        tech_sig,
        usdjpy: float = 150.0,
        use_cache: bool = True,
    ) -> TenbaggerRawData:
        """1銘柄のテンバガーデータを取得"""
        cache_path = self._cache_path(symbol)

        if use_cache and self._is_fresh(cache_path):
            logger.debug(f"[{symbol}] テンバガーデータ: キャッシュ使用")
            return self._load(cache_path)

        logger.info(f"[{symbol}] テンバガーデータ取得中...")

        # 基本情報
        name = fd.get("name") or symbol
        currency = fd.get("currency") or "USD"
        sector = fd.get("sector") or ""
        market_cap = fd.get("market_cap")
        gross_margin = fd.get("gross_margin") or fd.get("operating_margin")  # fallback

        # 粗利率は fd から取る（yfinanceが別途持っていれば上書き）
        # fd には gross_margin は直接ないが operating_margin はある
        # → yfinance info から grossMargins を引く
        gross_margin_fd = None

        # 時価総額（億円換算）
        market_cap_oku_jpy: Optional[float] = None
        if market_cap is not None:
            if currency == "JPY":
                market_cap_oku_jpy = market_cap / 1e8
            else:
                market_cap_oku_jpy = market_cap * usdjpy / 1e8

        # テクニカル引き継ぎ
        above_ma200 = getattr(tech_sig, "above_ma200", None)
        volume_ratio = getattr(tech_sig, "volume_ratio", None)
        pct_from_52w_high = getattr(tech_sig, "pct_from_52w_high", None)

        # yfinance 追加データ取得
        ticker_sym = to_yfinance_symbol(symbol)
        industry = ""
        op_margin_current: Optional[float] = fd.get("operating_margin")
        op_margin_1y_ago: Optional[float] = None
        revenue_growth_current: Optional[float] = None
        revenue_growth_4q_ago: Optional[float] = None
        fcf_current: Optional[float] = None
        fcf_prior: Optional[float] = None
        fcf_2y_prior: Optional[float] = None
        roic: Optional[float] = None
        shares_current: Optional[float] = None
        shares_3y_ago: Optional[float] = None
        float_ratio: Optional[float] = None
        summary = ""

        try:
            ticker = yf.Ticker(ticker_sym)
            info = ticker.info or {}

            industry = info.get("industry") or ""
            summary = info.get("longBusinessSummary") or ""
            gross_margin_fd = info.get("grossMargins")

            # ── ROIC計算 ────────────────────────────────────────────
            try:
                op_income = info.get("operatingIncome")
                book_value = info.get("bookValue")
                shares_out = info.get("sharesOutstanding")
                total_debt = info.get("totalDebt")
                total_cash = info.get("totalCash")
                if (op_income is not None and book_value is not None
                        and shares_out is not None and total_debt is not None
                        and total_cash is not None):
                    nopat = op_income * (1 - 0.25)
                    total_equity = book_value * shares_out
                    invested_capital = total_equity + total_debt - total_cash
                    if invested_capital > 0:
                        roic = nopat / invested_capital
            except Exception as e:
                logger.debug(f"[{symbol}] ROIC計算失敗: {e}")

            # ── 浮動株 ──────────────────────────────────────────────
            try:
                float_shares = info.get("floatShares")
                shares_outstanding = info.get("sharesOutstanding")
                if float_shares and shares_outstanding and shares_outstanding > 0:
                    float_ratio = float_shares / shares_outstanding
            except Exception as e:
                logger.debug(f"[{symbol}] 浮動株取得失敗: {e}")

            # ── 年次FCF（cashflow） ──────────────────────────────────
            try:
                cf = ticker.cashflow
                if cf is not None and not cf.empty:
                    def _get_fcf_from_cf(col) -> Optional[float]:
                        """Operating CF + Capex = FCF"""
                        op_cf = None
                        capex = None
                        for key in ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities",
                                    "Total Cash From Operating Activities"]:
                            if key in col.index and col[key] is not None:
                                try:
                                    op_cf = float(col[key])
                                    break
                                except (ValueError, TypeError):
                                    pass
                        for key in ["Capital Expenditure", "Capital Expenditures",
                                    "Purchase Of Plant And Equipment"]:
                            if key in col.index and col[key] is not None:
                                try:
                                    capex = float(col[key])
                                    break
                                except (ValueError, TypeError):
                                    pass
                        if op_cf is not None and capex is not None:
                            return op_cf + capex  # capex は負値が多い
                        if op_cf is not None:
                            return op_cf
                        return None

                    cols = [cf.iloc[:, i] for i in range(min(3, cf.shape[1]))]
                    if len(cols) >= 1:
                        fcf_current = _get_fcf_from_cf(cols[0])
                    if len(cols) >= 2:
                        fcf_prior = _get_fcf_from_cf(cols[1])
                    if len(cols) >= 3:
                        fcf_2y_prior = _get_fcf_from_cf(cols[2])
            except Exception as e:
                logger.debug(f"[{symbol}] FCF取得失敗: {e}")

            # ── 四半期売上・営業利益（成長率・利益率比較） ────────────
            try:
                qi = None
                try:
                    qi = ticker.quarterly_income_stmt
                except Exception:
                    pass
                if qi is None or qi.empty:
                    try:
                        qi = ticker.quarterly_financials
                    except Exception:
                        pass

                if qi is not None and not qi.empty:
                    # 売上行を探す
                    rev_row = None
                    for key in ["Total Revenue", "Revenue"]:
                        if key in qi.index:
                            rev_row = qi.loc[key]
                            break

                    # 営業利益行を探す
                    op_row = None
                    for key in ["Operating Income", "Operating Revenue",
                                "Total Operating Income As Reported"]:
                        if key in qi.index:
                            op_row = qi.loc[key]
                            break

                    if rev_row is not None and len(rev_row) >= 5:
                        # q0=最新, q4=1年前, q8=2年前
                        def _safe_float(val) -> Optional[float]:
                            try:
                                v = float(val)
                                return v if v == v else None  # NaN check
                            except (TypeError, ValueError):
                                return None

                        q0 = _safe_float(rev_row.iloc[0])
                        q4 = _safe_float(rev_row.iloc[4]) if len(rev_row) > 4 else None

                        if q0 is not None and q4 is not None and q4 != 0:
                            revenue_growth_current = (q0 - q4) / abs(q4)

                        if len(rev_row) >= 9:
                            q8 = _safe_float(rev_row.iloc[8])
                            if q4 is not None and q8 is not None and q8 != 0:
                                revenue_growth_4q_ago = (q4 - q8) / abs(q8)

                    if op_row is not None and rev_row is not None:
                        def _calc_margin(op_val, rev_val) -> Optional[float]:
                            try:
                                op = float(op_val)
                                rv = float(rev_val)
                                if rv and rv == rv and op == op:
                                    return op / rv
                            except (TypeError, ValueError):
                                pass
                            return None

                        # None で上書きしないよう、計算成功時のみ代入
                        if len(op_row) >= 1 and len(rev_row) >= 1:
                            _m = _calc_margin(op_row.iloc[0], rev_row.iloc[0])
                            if _m is not None:
                                op_margin_current = _m

                        if len(op_row) >= 5 and len(rev_row) >= 5:
                            _m1y = _calc_margin(op_row.iloc[4], rev_row.iloc[4])
                            if _m1y is not None:
                                op_margin_1y_ago = _m1y

            except Exception as e:
                logger.debug(f"[{symbol}] 四半期財務データ取得失敗: {e}")

            # ── 株式数履歴（希薄化チェック） ─────────────────────────
            try:
                shares_hist = ticker.get_shares_full(start="2021-01-01")
                if shares_hist is not None and len(shares_hist) > 0:
                    shares_current = float(shares_hist.iloc[-1])
                    # 3年前（約756営業日）のデータ
                    if len(shares_hist) >= 2:
                        shares_3y_ago = float(shares_hist.iloc[0])
            except Exception as e:
                logger.debug(f"[{symbol}] 株式数履歴取得失敗: {e}")

            # ── ROIC フォールバック（ROEを代替使用） ────────────────────
            if roic is None:
                roe = fd.get("roe")
                if roe is not None:
                    roic = roe  # ROEをROICの代理指標として使用
                    logger.debug(f"[{symbol}] ROIC: ROEをフォールバック使用 ({roe:.2%})")

            # ── 年次データフォールバック ────────────────────────────
            # 四半期データが不足している場合、年次データで補完
            if revenue_growth_current is None:
                # yfinance info の revenueGrowth (TTM YoY) を使う
                rg = fd.get("revenue_growth")
                if rg is not None:
                    revenue_growth_current = rg
                    logger.debug(f"[{symbol}] 売上成長率: 年次フォールバック使用 ({rg:.2%})")

            if revenue_growth_4q_ago is None or op_margin_1y_ago is None:
                try:
                    ann = ticker.income_stmt
                    if ann is not None and not ann.empty and ann.shape[1] >= 2:
                        def _safe_f(val) -> Optional[float]:
                            try:
                                v = float(val)
                                return v if v == v else None  # NaN check
                            except (TypeError, ValueError):
                                return None

                        rev_ann = None
                        for key in ["Total Revenue", "Revenue"]:
                            if key in ann.index:
                                rev_ann = ann.loc[key]
                                break

                        op_ann = None
                        for key in ["Operating Income", "Ebit"]:
                            if key in ann.index:
                                op_ann = ann.loc[key]
                                break

                        # revenue_growth_4q_ago: 前年の成長率（加速度判定用）
                        if revenue_growth_4q_ago is None and rev_ann is not None and len(rev_ann) >= 3:
                            r0 = _safe_f(rev_ann.iloc[0])
                            r1 = _safe_f(rev_ann.iloc[1])
                            r2 = _safe_f(rev_ann.iloc[2])
                            if r1 is not None and r2 is not None and r2 != 0:
                                revenue_growth_4q_ago = (r1 - r2) / abs(r2)
                                logger.debug(f"[{symbol}] 加速度比較: 年次フォールバック使用")

                        # op_margin_1y_ago フォールバック
                        if op_margin_1y_ago is None and op_ann is not None and rev_ann is not None:
                            if len(op_ann) >= 2 and len(rev_ann) >= 2:
                                op1 = _safe_f(op_ann.iloc[1])
                                rv1 = _safe_f(rev_ann.iloc[1])
                                if op1 is not None and rv1 is not None and rv1 != 0:
                                    op_margin_1y_ago = op1 / rv1
                                    logger.debug(f"[{symbol}] 営業利益率前年: 年次フォールバック使用")
                except Exception as e:
                    logger.debug(f"[{symbol}] 年次フォールバック取得失敗: {e}")

        except Exception as e:
            logger.warning(f"[{symbol}] テンバガーデータ取得エラー: {e}")

        # テーマ分類
        matched_theme, theme_tam_large = _classify_theme(sector, industry, summary)

        # 粗利率（fd から取得した operating_margin より grossMargins を優先）
        gross_margin_final = gross_margin_fd if gross_margin_fd is not None else None

        data = TenbaggerRawData(
            symbol=symbol,
            name=name,
            currency=currency,
            sector=sector,
            industry=industry,
            market_cap=market_cap,
            market_cap_oku_jpy=market_cap_oku_jpy,
            revenue_growth_current=revenue_growth_current,
            revenue_growth_4q_ago=revenue_growth_4q_ago,
            gross_margin=gross_margin_final,
            op_margin_current=op_margin_current,
            op_margin_1y_ago=op_margin_1y_ago,
            fcf_current=fcf_current,
            fcf_prior=fcf_prior,
            fcf_2y_prior=fcf_2y_prior,
            roic=roic,
            shares_current=shares_current,
            shares_3y_ago=shares_3y_ago,
            float_ratio=float_ratio,
            founder_ownership=None,
            matched_theme=matched_theme,
            theme_tam_large=theme_tam_large,
            above_ma200=above_ma200,
            volume_ratio=volume_ratio,
            pct_from_52w_high=pct_from_52w_high,
            data_quality="full",
        )

        self._save(cache_path, data)
        logger.info(f"[{symbol}] テンバガーデータ取得完了")
        return data

    def fetch_universe(
        self,
        symbols: list[str],
        fd_dict: dict[str, dict],
        tech_dict: dict,
        usdjpy: float = 150.0,
        use_cache: bool = True,
        max_workers: int = 5,
    ) -> dict[str, TenbaggerRawData]:
        """
        複数銘柄のテンバガーデータを並列取得。

        Args:
            max_workers: 並列数（yfinance過負荷防止のため5が推奨）
        """
        result: dict[str, TenbaggerRawData] = {}

        class _EmptySig:
            above_ma200 = None
            volume_ratio = None
            pct_from_52w_high = None

        def _fetch_one(symbol: str):
            fd = fd_dict.get(symbol, {})
            tech_sig = tech_dict.get(symbol) or _EmptySig()
            return symbol, self.fetch(
                symbol, fd, tech_sig, usdjpy=usdjpy, use_cache=use_cache
            )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_fetch_one, s): s for s in symbols}
            for future in as_completed(futures):
                sym = futures[future]
                try:
                    sym_out, data = future.result()
                    result[sym_out] = data
                except Exception as e:
                    logger.debug(f"[{sym}] テンバガーデータ取得失敗（スキップ）: {e}")

        logger.info(f"テンバガーデータ一括取得完了: {len(result)}/{len(symbols)} 銘柄")
        return result
