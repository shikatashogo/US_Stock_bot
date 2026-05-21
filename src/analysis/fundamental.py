"""
ファンダメンタル分析エンジン
================================
財務データから銘柄の「割安度・収益性・財務健全性」を
複数の手法でスコアリングする。

スコアリング手法:
  1. バリュエーション評価（PER・PBR・PSR）
  2. 収益性評価（ROE・ROA・利益率）
  3. 財務健全性評価（Piotroski F-score 簡易版）
  4. 成長性評価（売上・利益成長率）
  5. 総合ファンダスコア（0〜10点）

「割安」の定義:
  現在のバリュエーション指標が業種平均・歴史平均と比較して
  統計的に低い状態。「必ず上がる」保証ではない。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ─── セクター別 PER・PBR 参考値（2024年末時点の大まかな水準） ────────
# 実際には変動するため、±30%程度の誤差を前提に使用する
SECTOR_BENCHMARKS: dict[str, dict] = {
    # 日本株セクター
    "金融":         {"per_avg": 12, "pbr_avg": 0.7, "roe_avg": 0.08},
    "テクノロジー": {"per_avg": 25, "pbr_avg": 4.0, "roe_avg": 0.15},
    "半導体":       {"per_avg": 28, "pbr_avg": 5.0, "roe_avg": 0.18},
    "電機":         {"per_avg": 18, "pbr_avg": 1.5, "roe_avg": 0.10},
    "機械":         {"per_avg": 20, "pbr_avg": 1.8, "roe_avg": 0.10},
    "自動車":       {"per_avg": 10, "pbr_avg": 1.0, "roe_avg": 0.12},
    "化学":         {"per_avg": 18, "pbr_avg": 1.5, "roe_avg": 0.12},
    "医薬品":       {"per_avg": 30, "pbr_avg": 3.0, "roe_avg": 0.12},
    "小売":         {"per_avg": 20, "pbr_avg": 2.0, "roe_avg": 0.12},
    "通信":         {"per_avg": 14, "pbr_avg": 1.5, "roe_avg": 0.12},
    "不動産":       {"per_avg": 20, "pbr_avg": 1.5, "roe_avg": 0.08},
    "商社":         {"per_avg": 10, "pbr_avg": 0.9, "roe_avg": 0.15},
    # 米国株セクター
    "Technology":     {"per_avg": 30, "pbr_avg": 8.0, "roe_avg": 0.25},
    "Semiconductors": {"per_avg": 35, "pbr_avg": 8.0, "roe_avg": 0.25},
    "Consumer Disc.": {"per_avg": 25, "pbr_avg": 5.0, "roe_avg": 0.20},
    "Financials":     {"per_avg": 14, "pbr_avg": 1.5, "roe_avg": 0.13},
    "Healthcare":     {"per_avg": 25, "pbr_avg": 5.0, "roe_avg": 0.18},
    "Consumer Staples":{"per_avg": 22, "pbr_avg": 4.0, "roe_avg": 0.15},
    "Energy":         {"per_avg": 12, "pbr_avg": 1.8, "roe_avg": 0.15},
    "Communication":  {"per_avg": 15, "pbr_avg": 2.0, "roe_avg": 0.12},
}

DEFAULT_BENCHMARK = {"per_avg": 20, "pbr_avg": 2.0, "roe_avg": 0.12}


@dataclass
class FundamentalScore:
    """銘柄のファンダメンタルスコア評価結果"""
    symbol: str
    name: str

    # 個別スコア（各 0〜2点）
    valuation_score: float = 0.0     # バリュエーション
    profitability_score: float = 0.0  # 収益性
    health_score: float = 0.0        # 財務健全性
    growth_score: float = 0.0        # 成長性

    # 総合スコア（0〜10点）
    total_score: float = 0.0

    # 判定理由
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # データ品質
    data_quality: str = "full"

    @property
    def grade(self) -> str:
        """スコアをグレードに変換"""
        if self.total_score >= 7.5:  return "A"
        if self.total_score >= 6.0:  return "B"
        if self.total_score >= 4.5:  return "C"
        if self.total_score >= 3.0:  return "D"
        return "F"


class FundamentalAnalyzer:
    """
    ファンダメンタル分析クラス

    使い方:
        analyzer = FundamentalAnalyzer()
        score = analyzer.analyze(fundamentals_dict)
    """

    def analyze(self, fd: dict) -> FundamentalScore:
        """
        財務データdictをFundamentalScoreに変換

        Args:
            fd: StockFetcher.fetch_fundamentals() の返り値
        Returns:
            FundamentalScore
        """
        symbol = fd.get("symbol", "")
        name   = fd.get("name", symbol)
        sector = fd.get("sector", "")
        benchmark = SECTOR_BENCHMARKS.get(sector, DEFAULT_BENCHMARK)

        score = FundamentalScore(
            symbol=symbol,
            name=name,
            data_quality=fd.get("data_quality", "unavailable"),
        )

        if score.data_quality == "unavailable":
            score.warnings.append("財務データ取得不可。スコア計算をスキップ。")
            return score

        # 各スコア計算
        score.valuation_score    = self._score_valuation(fd, benchmark, score)
        score.profitability_score = self._score_profitability(fd, benchmark, score)
        score.health_score       = self._score_health(fd, score)
        score.growth_score       = self._score_growth(fd, score)

        # 総合スコア（各最大2.5点×4 = 10点満点）
        score.total_score = round(
            score.valuation_score * 1.25 +     # バリュエーション（重要度高）
            score.profitability_score * 1.25 +  # 収益性（重要度高）
            score.health_score * 1.0 +          # 財務健全性
            score.growth_score * 1.0,           # 成長性
            2,
        )
        score.total_score = min(10.0, score.total_score)

        # データ品質警告
        if score.data_quality == "partial":
            score.warnings.append("財務データが部分的。スコアの精度が低下している可能性あり。")

        return score

    # ─── バリュエーション ─────────────────────────────────────────

    def _score_valuation(self, fd: dict, bench: dict, score: FundamentalScore) -> float:
        """PER・PBR・アナリスト目標株価からバリュエーションをスコア化（0〜2点）"""
        points = 0.0
        count = 0

        # PER評価
        per = fd.get("per_trailing") or fd.get("per_forward")
        per_avg = bench["per_avg"]
        if per is not None and per > 0:
            count += 1
            ratio = per / per_avg
            if ratio < 0.7:
                points += 2.0
                score.strengths.append(f"PER {per:.1f}x（業種平均{per_avg}xの{ratio:.0%}）→ 割安")
            elif ratio < 0.9:
                points += 1.2
                score.strengths.append(f"PER {per:.1f}x（やや割安）")
            elif ratio < 1.2:
                points += 0.8
            else:
                points += 0.2
                score.weaknesses.append(f"PER {per:.1f}x（業種平均比 {ratio:.0%}）→ 割高傾向")

        # PBR評価
        pbr = fd.get("pbr")
        pbr_avg = bench["pbr_avg"]
        if pbr is not None and pbr > 0:
            count += 1
            ratio = pbr / pbr_avg
            if pbr < 1.0:
                points += 2.0
                score.strengths.append(f"PBR {pbr:.2f}x（純資産以下）→ 清算価値割れ水準")
            elif ratio < 0.7:
                points += 1.5
                score.strengths.append(f"PBR {pbr:.2f}x（業種平均比 割安）")
            elif ratio < 1.0:
                points += 0.8
            else:
                points += 0.3
                if ratio > 2.0:
                    score.weaknesses.append(f"PBR {pbr:.2f}x（業種平均比 割高）")

        # アナリスト目標株価との乖離
        current = fd.get("current_price")
        target  = fd.get("target_mean_price") or fd.get("target_median_price")
        analyst_count = fd.get("analyst_count") or 0
        if current and target and current > 0 and analyst_count >= 3:
            upside = (target - current) / current
            count += 1
            if upside > 0.20:
                points += 2.0
                score.strengths.append(
                    f"アナリスト目標株価まで +{upside:.0%}上昇余地（{analyst_count}名評価）"
                )
            elif upside > 0.10:
                points += 1.0
                score.strengths.append(f"アナリスト目標株価まで +{upside:.0%}上昇余地")
            elif upside < -0.05:
                score.weaknesses.append(f"アナリスト目標株価が現値下 ({upside:.0%})")

        return round(points / max(count, 1), 2) if count > 0 else 0.5

    # ─── 収益性 ───────────────────────────────────────────────────

    def _score_profitability(self, fd: dict, bench: dict, score: FundamentalScore) -> float:
        """ROE・利益率から収益性をスコア化（0〜2点）"""
        points = 0.0
        count = 0

        # ROE評価
        roe = fd.get("roe")
        roe_avg = bench["roe_avg"]
        if roe is not None:
            count += 1
            if roe >= roe_avg * 1.5:
                points += 2.0
                score.strengths.append(f"ROE {roe*100:.1f}%（業種平均{roe_avg*100:.0f}%超）→ 高収益")
            elif roe >= roe_avg:
                points += 1.2
                score.strengths.append(f"ROE {roe*100:.1f}%（業種平均以上）")
            elif roe >= roe_avg * 0.5:
                points += 0.5
            else:
                score.weaknesses.append(f"ROE {roe*100:.1f}%（業種平均{roe_avg*100:.0f}%を下回る）")

        # 営業利益率
        margin = fd.get("operating_margin")
        if margin is not None:
            count += 1
            if margin >= 0.20:
                points += 2.0
                score.strengths.append(f"営業利益率 {margin*100:.1f}%（高水準）")
            elif margin >= 0.10:
                points += 1.2
            elif margin >= 0.05:
                points += 0.6
            elif margin < 0:
                score.weaknesses.append(f"営業赤字（利益率 {margin*100:.1f}%）")

        # 配当利回り（インカムゲイン評価）
        div_yield = fd.get("dividend_yield")
        if div_yield is not None and div_yield > 0:
            if div_yield >= 0.04:
                score.strengths.append(f"高配当 {div_yield*100:.1f}%（インカムゲイン魅力あり）")
            elif div_yield >= 0.02:
                score.strengths.append(f"配当利回り {div_yield*100:.1f}%")

        return round(points / max(count, 1), 2) if count > 0 else 0.5

    # ─── 財務健全性 ───────────────────────────────────────────────

    def _score_health(self, fd: dict, score: FundamentalScore) -> float:
        """負債比率・流動比率・FCFから財務健全性をスコア化（0〜2点）"""
        points = 0.0
        count = 0

        # D/Eレシオ（負債/自己資本）
        de = fd.get("debt_to_equity")
        if de is not None:
            count += 1
            if de < 30:      # 30%未満（無借金に近い）
                points += 2.0
                score.strengths.append(f"D/Eレシオ {de:.0f}%（財務健全・低負債）")
            elif de < 100:
                points += 1.2
            elif de < 200:
                points += 0.5
            else:
                score.weaknesses.append(f"D/Eレシオ {de:.0f}%（高負債に注意）")

        # 流動比率（短期債務支払い能力）
        cr = fd.get("current_ratio")
        if cr is not None:
            count += 1
            if cr >= 2.0:
                points += 2.0
                score.strengths.append(f"流動比率 {cr:.1f}（流動性優良）")
            elif cr >= 1.5:
                points += 1.2
            elif cr >= 1.0:
                points += 0.6
            else:
                score.warnings.append(f"流動比率 {cr:.1f}（1.0未満 → 短期資金繰りリスク）")

        # フリーキャッシュフロー（FCF）
        fcf = fd.get("free_cashflow")
        market_cap = fd.get("market_cap")
        if fcf is not None:
            count += 1
            if fcf > 0:
                points += 1.5
                score.strengths.append("FCFプラス（自己資金で事業運営・株主還元可能）")
                if market_cap and market_cap > 0:
                    fcf_yield = fcf / market_cap
                    if fcf_yield > 0.05:
                        score.strengths.append(f"FCF利回り {fcf_yield*100:.1f}%（バフェット基準クリア）")
            else:
                score.weaknesses.append("FCFマイナス（資金調達依存の可能性）")

        return round(points / max(count, 1), 2) if count > 0 else 0.5

    # ─── 成長性 ───────────────────────────────────────────────────

    def _score_growth(self, fd: dict, score: FundamentalScore) -> float:
        """売上・利益成長率から成長性をスコア化（0〜2点）"""
        points = 0.0
        count = 0

        # 売上成長率
        rev_g = fd.get("revenue_growth")
        if rev_g is not None:
            count += 1
            if rev_g >= 0.20:
                points += 2.0
                score.strengths.append(f"売上成長率 {rev_g*100:.1f}%（高成長）")
            elif rev_g >= 0.10:
                points += 1.3
                score.strengths.append(f"売上成長率 {rev_g*100:.1f}%")
            elif rev_g >= 0.05:
                points += 0.7
            elif rev_g < 0:
                score.weaknesses.append(f"売上減少 {rev_g*100:.1f}%")

        # 利益成長率（四半期YoY）
        earn_g = fd.get("earnings_quarterly_growth") or fd.get("earnings_growth")
        if earn_g is not None:
            count += 1
            if earn_g >= 0.20:
                points += 2.0
                score.strengths.append(f"利益成長率 {earn_g*100:.1f}%（高成長）")
            elif earn_g >= 0.10:
                points += 1.2
            elif earn_g >= 0:
                points += 0.5
            else:
                score.weaknesses.append(f"利益減少 {earn_g*100:.1f}%")

        return round(points / max(count, 1), 2) if count > 0 else 0.5


# ─── ユーティリティ関数 ───────────────────────────────────────────

def batch_analyze(fundamentals_dict: dict[str, dict]) -> dict[str, FundamentalScore]:
    """
    複数銘柄を一括ファンダメンタル分析

    Args:
        fundamentals_dict: {symbol: fundamentals_dict}
    Returns:
        {symbol: FundamentalScore}
    """
    analyzer = FundamentalAnalyzer()
    results = {}
    for symbol, fd in fundamentals_dict.items():
        results[symbol] = analyzer.analyze(fd)
    return results
