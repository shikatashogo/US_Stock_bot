"""
テンバガー候補スクリーニングモジュール
========================================
TenbaggerRawData を受け取り、定量スコアリングで
「今後3〜10年で株価10倍の可能性がある銘柄」を抽出する。

スコアリング体系（合計 100pt 満点）:
  SIZE              15pt
  REVENUE GROWTH    20pt
  ACCELERATION      15pt
  GROSS MARGIN      10pt
  OP MARGIN IMPR.   10pt
  FCF               10pt
  ROIC               5pt
  DILUTION        -10~0pt
  FLOAT             10pt（実質 5pt）
  THEME             10pt
  CHART             10pt

合格ライン: 60点以上（データ不足による失点を考慮）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from src.data.tenbagger_fetcher import TenbaggerRawData

# ─── 除外セクター ────────────────────────────────────────────────────

_EXCLUDED_SECTOR_KEYWORDS = [
    "金融", "銀行", "保険",
    "Financial", "Bank", "Insurance",
    "Real Estate", "不動産", "REIT",
]


def _is_excluded_sector(sector: Optional[str]) -> bool:
    if not sector:
        return False
    return any(kw.lower() in sector.lower() for kw in _EXCLUDED_SECTOR_KEYWORDS)


# ─── 結果データクラス ─────────────────────────────────────────────────

@dataclass
class TenbaggerResult:
    """テンバガースクリーニング結果"""

    symbol: str
    name: str
    currency: str
    sector: str

    total_score: float = 0.0
    grade: str = ""        # "超有力テンバガー候補" / "有力テンバガー候補" / "テンバガー監視候補" / "対象外"
    qualified: bool = False

    # スコア内訳
    score_size: int = 0
    score_revenue_growth: int = 0
    score_acceleration: int = 0
    score_gross_margin: int = 0
    score_op_margin_improvement: int = 0
    score_fcf: int = 0
    score_roic: int = 0
    score_dilution: int = 0
    score_float: int = 0
    score_theme: int = 0
    score_chart: int = 0

    # 生データ参照
    raw: Optional[TenbaggerRawData] = None

    # 表示用文字列
    market_cap_str: str = "不明"
    revenue_growth_str: str = "不明"
    revenue_4q_str: str = "不明"
    acceleration_str: str = "不明"
    gross_margin_str: str = "不明"
    op_margin_str: str = "不明"
    op_margin_change_str: str = "不明"
    fcf_str: str = "不明"
    roic_str: str = "不明"
    dilution_str: str = "不明"
    float_ratio_str: str = "不明"
    chart_conditions: list = field(default_factory=list)
    theme_str: str = "不明"


# ─── スクリーナー ─────────────────────────────────────────────────────

class TenbaggerScreener:
    """テンバガー候補スクリーニングクラス"""

    def _score_size(self, mc: Optional[float]) -> int:
        """時価総額スコア（億円）"""
        if mc is None:
            return 0
        if 50 <= mc <= 300:
            return 15
        if 300 < mc <= 700:
            return 10
        if 700 < mc <= 3000:
            return 5
        return 0

    def _score_revenue_growth(self, growth: Optional[float]) -> int:
        """売上成長率スコア（decimal）"""
        if growth is None:
            return 0
        g = growth * 100  # %換算
        if g >= 40:
            return 20
        if g >= 30:
            return 15
        if g >= 20:
            return 10
        if g >= 10:
            return 5
        return 0

    def _score_acceleration(
        self,
        current: Optional[float],
        ago: Optional[float],
    ) -> tuple[int, str]:
        """成長加速度スコア。(score, acceleration_str) を返す"""
        if current is None or ago is None:
            return 0, "不明（データ不足）"
        acc = (current - ago) * 100  # %pt
        acc_str = f"{acc:+.1f}%pt"
        if acc >= 20:
            return 15, acc_str
        if acc >= 10:
            return 10, acc_str
        if acc >= 5:
            return 5, acc_str
        return 0, acc_str

    def _score_gross_margin(self, gm: Optional[float]) -> int:
        """粗利率スコア（decimal）"""
        if gm is None:
            return 0
        g = gm * 100  # %換算
        if g >= 70:
            return 10
        if g >= 50:
            return 7
        if g >= 40:
            return 5
        if g >= 30:
            return 2
        return 0

    def _score_op_margin_improvement(
        self,
        cur: Optional[float],
        prev: Optional[float],
    ) -> tuple[int, str]:
        """営業利益率改善スコア。(score, change_str) を返す"""
        if cur is None:
            return 0, "不明"

        if prev is None:
            change_str = "不明（前年データなし）"
            if cur >= 0:
                return 2, change_str
            return 0, change_str

        improvement = (cur - prev) * 100  # %pt
        change_str = f"{improvement:+.1f}%pt"
        if improvement >= 10:
            return 10, change_str
        if improvement >= 5:
            return 7, change_str
        if improvement >= 2:
            return 4, change_str
        if improvement >= 0:
            return 2, change_str
        return 0, change_str

    def _score_fcf(
        self,
        fcf: Optional[float],
        prior: Optional[float],
        fcf_2y: Optional[float],
    ) -> int:
        """FCFスコア"""
        if fcf is None:
            return 0
        if fcf > 0:
            return 10
        # FCF < 0 の場合
        if prior is not None and prior < 0:
            improvement_pct = (fcf - prior) / abs(prior) * 100
            if improvement_pct >= 30:
                return 7
        if prior is not None and fcf_2y is not None:
            if fcf > prior > fcf_2y:
                return 5
        return 0

    def _score_roic(self, roic: Optional[float]) -> int:
        """ROICスコア（decimal）"""
        if roic is None:
            return 0
        r = roic * 100  # %換算
        if r >= 15:
            return 5
        if r >= 10:
            return 3
        if r >= 5:
            return 1
        return 0

    def _score_dilution(
        self,
        shares_cur: Optional[float],
        shares_old: Optional[float],
    ) -> tuple[int, str]:
        """希薄化スコア（減点）。(score, dilution_str) を返す"""
        if shares_cur is None or shares_old is None or shares_old == 0:
            return 0, "不明"
        increase = (shares_cur - shares_old) / shares_old * 100
        dilution_str = f"{increase:+.1f}%（3年）"
        if increase > 30:
            return -10, dilution_str
        if increase > 15:
            return -5, dilution_str
        return 0, dilution_str

    def _score_float(self, float_ratio: Optional[float]) -> int:
        """需給スコア（浮動株比率）"""
        score = 0
        # 創業者保有は常に不明 → +0
        if float_ratio is not None and float_ratio <= 0.40:
            score += 5
        return score

    def _score_theme(self, matched_theme: str, tam_large: bool) -> int:
        """テーマスコア"""
        if not matched_theme:
            return 0
        return 10 if tam_large else 5

    def _score_chart(
        self,
        pct_from_52w_high: Optional[float],
        above_ma200: Optional[bool],
        volume_ratio: Optional[float],
    ) -> tuple[int, list[str]]:
        """チャートスコア。(score, conditions) を返す"""
        conditions: list[str] = []
        met = 0

        if pct_from_52w_high is not None:
            if pct_from_52w_high >= -5:
                met += 1
                conditions.append(f"✅ 52週高値から{pct_from_52w_high:.1f}%（-5%以内）")
            else:
                conditions.append(f"❌ 52週高値から{pct_from_52w_high:.1f}%")

        if above_ma200 is not None:
            if above_ma200:
                met += 1
                conditions.append("✅ 200日移動平均線上方")
            else:
                conditions.append("❌ 200日移動平均線下方")

        if volume_ratio is not None:
            if volume_ratio >= 2.0:
                met += 1
                conditions.append(f"✅ 出来高急増（平均比 {volume_ratio:.1f}倍）")
            else:
                conditions.append(f"❌ 出来高: 平均比 {volume_ratio:.1f}倍")

        if met == 3:
            return 10, conditions
        if met == 2:
            return 5, conditions
        if met == 1:
            return 2, conditions
        return 0, conditions

    def _assign_grade(self, score: float) -> tuple[str, bool]:
        """スコアからグレードと合格フラグを決定"""
        if score >= 80:
            return "超有力テンバガー候補", True
        if score >= 70:
            return "有力テンバガー候補", True
        if score >= 55:
            return "テンバガー監視候補", True
        return "対象外", False

    def _fmt_pct(self, val: Optional[float]) -> str:
        if val is None:
            return "不明"
        return f"{val * 100:.1f}%"

    def _fmt_oku(self, val: Optional[float]) -> str:
        if val is None:
            return "不明"
        return f"{val:,.0f}億円"

    def _fmt_fcf(self, val: Optional[float], currency: str) -> str:
        if val is None:
            return "不明"
        if currency == "JPY":
            return f"¥{val / 1e8:,.1f}億"
        return f"${val / 1e9:,.2f}B"

    def evaluate(self, raw: TenbaggerRawData) -> TenbaggerResult:
        """1銘柄をスコアリングして TenbaggerResult を返す"""
        r = TenbaggerResult(
            symbol=raw.symbol,
            name=raw.name,
            currency=raw.currency,
            sector=raw.sector,
            raw=raw,
        )

        # 1. SIZE
        r.score_size = self._score_size(raw.market_cap_oku_jpy)
        r.market_cap_str = self._fmt_oku(raw.market_cap_oku_jpy)

        # 2. REVENUE GROWTH
        r.score_revenue_growth = self._score_revenue_growth(raw.revenue_growth_current)
        r.revenue_growth_str = self._fmt_pct(raw.revenue_growth_current)
        r.revenue_4q_str = self._fmt_pct(raw.revenue_growth_4q_ago)

        # 3. ACCELERATION
        r.score_acceleration, r.acceleration_str = self._score_acceleration(
            raw.revenue_growth_current, raw.revenue_growth_4q_ago
        )

        # 4. GROSS MARGIN
        r.score_gross_margin = self._score_gross_margin(raw.gross_margin)
        r.gross_margin_str = self._fmt_pct(raw.gross_margin)

        # 5. OP MARGIN IMPROVEMENT
        r.score_op_margin_improvement, r.op_margin_change_str = self._score_op_margin_improvement(
            raw.op_margin_current, raw.op_margin_1y_ago
        )
        r.op_margin_str = self._fmt_pct(raw.op_margin_current)

        # 6. FCF
        r.score_fcf = self._score_fcf(raw.fcf_current, raw.fcf_prior, raw.fcf_2y_prior)
        r.fcf_str = self._fmt_fcf(raw.fcf_current, raw.currency)

        # 7. ROIC
        r.score_roic = self._score_roic(raw.roic)
        r.roic_str = self._fmt_pct(raw.roic)

        # 8. DILUTION
        r.score_dilution, r.dilution_str = self._score_dilution(
            raw.shares_current, raw.shares_3y_ago
        )

        # 9. FLOAT
        r.score_float = self._score_float(raw.float_ratio)
        r.float_ratio_str = self._fmt_pct(raw.float_ratio)

        # 10. THEME
        r.score_theme = self._score_theme(raw.matched_theme, raw.theme_tam_large)
        r.theme_str = raw.matched_theme if raw.matched_theme else "不明"

        # 11. CHART
        r.score_chart, r.chart_conditions = self._score_chart(
            raw.pct_from_52w_high, raw.above_ma200, raw.volume_ratio
        )

        # 合計スコア（0〜100 クランプ）
        raw_total = (
            r.score_size
            + r.score_revenue_growth
            + r.score_acceleration
            + r.score_gross_margin
            + r.score_op_margin_improvement
            + r.score_fcf
            + r.score_roic
            + r.score_dilution
            + r.score_float
            + r.score_theme
            + r.score_chart
        )
        r.total_score = float(max(0, min(100, raw_total)))

        r.grade, r.qualified = self._assign_grade(r.total_score)

        return r

    def screen(
        self,
        raw_dict: dict[str, TenbaggerRawData],
        fd_dict: dict[str, dict],
    ) -> list[TenbaggerResult]:
        """
        全銘柄をスクリーニングし、55点以上の候補を返す（スコア降順）。

        除外条件:
          - 除外セクター（金融・不動産等）
          - 時価総額 < 50億円（データあり時のみ除外）
          ※ 上限なし: 大型株はSIZEスコア0となり自然に低評価される
        """
        results: list[TenbaggerResult] = []

        for symbol, raw in raw_dict.items():
            # セクター除外
            if _is_excluded_sector(raw.sector):
                logger.debug(f"[{symbol}] セクター除外: {raw.sector}")
                continue

            # 時価総額下限除外（上限は設けず、スコアで評価）
            mc = raw.market_cap_oku_jpy
            if mc is not None and mc < 50:
                logger.debug(f"[{symbol}] 時価総額除外（下限未満）: {mc:.0f}億円")
                continue

            result = self.evaluate(raw)
            logger.debug(
                f"[{symbol}] スコア: {result.total_score:.0f} ({result.grade})"
            )

            if result.qualified:
                results.append(result)

        results.sort(key=lambda x: x.total_score, reverse=True)
        logger.info(f"テンバガースクリーニング完了: {len(results)}銘柄が55点以上")
        return results


# ─── パイプライン関数 ─────────────────────────────────────────────────

def run_tenbagger_pipeline(
    symbols: list[str],
    use_cache: bool = True,
    usdjpy: float = 150.0,
    max_workers: int = 5,
) -> list[TenbaggerResult]:
    """
    テンバガー候補抽出パイプライン（エントリーポイント）

    Args:
        symbols    : 対象銘柄リスト
        use_cache  : キャッシュ使用（False で強制再取得）
        usdjpy     : ドル円レート（時価総額の円換算用）
        max_workers: 並列取得スレッド数（大量銘柄時は5推奨）
    """
    from src.data.stock_fetcher import StockFetcher
    from src.analysis.technical import TechnicalAnalyzer
    from src.data.tenbagger_fetcher import TenbaggerFetcher

    fetcher = StockFetcher()
    price_data = fetcher.fetch_universe_prices(
        symbols, use_cache=use_cache, max_workers=max_workers * 2
    )
    fd_dict = fetcher.fetch_universe_fundamentals(
        symbols, use_cache=use_cache, max_workers=max_workers * 2
    )

    ta = TechnicalAnalyzer()
    tech_dict = {s: ta.analyze(s, df) for s, df in price_data.items()}

    tb_fetcher = TenbaggerFetcher()
    raw_data = tb_fetcher.fetch_universe(
        symbols, fd_dict, tech_dict,
        usdjpy=usdjpy, use_cache=use_cache, max_workers=max_workers
    )

    screener = TenbaggerScreener()
    return screener.screen(raw_data, fd_dict)
