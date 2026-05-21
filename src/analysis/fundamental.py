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
    # 日本株セクター                                    ev_ebitda_avg: 金融・不動産はNoneを設定（構造上不適）
    "金融":         {"per_avg": 12, "pbr_avg": 0.7, "roe_avg": 0.08, "ev_ebitda_avg": None},
    "テクノロジー": {"per_avg": 25, "pbr_avg": 4.0, "roe_avg": 0.15, "ev_ebitda_avg": 18},
    "半導体":       {"per_avg": 28, "pbr_avg": 5.0, "roe_avg": 0.18, "ev_ebitda_avg": 20},
    "電機":         {"per_avg": 18, "pbr_avg": 1.5, "roe_avg": 0.10, "ev_ebitda_avg": 12},
    "機械":         {"per_avg": 20, "pbr_avg": 1.8, "roe_avg": 0.10, "ev_ebitda_avg": 11},
    "自動車":       {"per_avg": 10, "pbr_avg": 1.0, "roe_avg": 0.12, "ev_ebitda_avg":  8},
    "化学":         {"per_avg": 18, "pbr_avg": 1.5, "roe_avg": 0.12, "ev_ebitda_avg": 11},
    "医薬品":       {"per_avg": 30, "pbr_avg": 3.0, "roe_avg": 0.12, "ev_ebitda_avg": 18},
    "小売":         {"per_avg": 20, "pbr_avg": 2.0, "roe_avg": 0.12, "ev_ebitda_avg": 12},
    "通信":         {"per_avg": 14, "pbr_avg": 1.5, "roe_avg": 0.12, "ev_ebitda_avg":  8},
    "不動産":       {"per_avg": 20, "pbr_avg": 1.5, "roe_avg": 0.08, "ev_ebitda_avg": None},
    "商社":         {"per_avg": 10, "pbr_avg": 0.9, "roe_avg": 0.15, "ev_ebitda_avg":  8},
    # 米国株セクター
    "Technology":      {"per_avg": 30, "pbr_avg": 8.0, "roe_avg": 0.25, "ev_ebitda_avg": 25},
    "Semiconductors":  {"per_avg": 35, "pbr_avg": 8.0, "roe_avg": 0.25, "ev_ebitda_avg": 22},
    "Consumer Disc.":  {"per_avg": 25, "pbr_avg": 5.0, "roe_avg": 0.20, "ev_ebitda_avg": 15},
    "Financials":      {"per_avg": 14, "pbr_avg": 1.5, "roe_avg": 0.13, "ev_ebitda_avg": None},
    "Healthcare":      {"per_avg": 25, "pbr_avg": 5.0, "roe_avg": 0.18, "ev_ebitda_avg": 18},
    "Consumer Staples":{"per_avg": 22, "pbr_avg": 4.0, "roe_avg": 0.15, "ev_ebitda_avg": 14},
    "Energy":          {"per_avg": 12, "pbr_avg": 1.8, "roe_avg": 0.15, "ev_ebitda_avg":  8},
    "Communication":   {"per_avg": 15, "pbr_avg": 2.0, "roe_avg": 0.12, "ev_ebitda_avg": 12},
}

DEFAULT_BENCHMARK = {"per_avg": 20, "pbr_avg": 2.0, "roe_avg": 0.12, "ev_ebitda_avg": 14}

# Altman Z-Score を適用しないセクター（金融機関・REIT は財務構造が異なる）
_SKIP_ALTMAN_Z_SECTORS = {"金融", "不動産", "Financials", "Real Estate"}


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

    # 追加スクリーニング指標
    altman_z: Optional[float] = None   # Altman Z-Score（None = 計算不可）

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

        # PEGレシオ（成長を考慮した割安度）
        # EPS成長率がマイナスや0の場合はPEGが無意味なのでスキップ
        peg = fd.get("peg_ratio")
        if peg is not None and peg > 0:
            count += 1
            if peg < 0.5:
                points += 2.0
                score.strengths.append(f"PEG {peg:.2f}（成長に対して大幅割安）")
            elif peg < 1.0:
                points += 1.5
                score.strengths.append(f"PEG {peg:.2f}（成長を考慮しても割安）")
            elif peg < 1.5:
                points += 0.8
            elif peg > 2.5:
                points += 0.1
                score.weaknesses.append(f"PEG {peg:.2f}（成長対比 割高）")
            else:
                points += 0.4

        # EV/EBITDA（負債構造を排除した割安度）
        ev_ebitda = fd.get("ev_ebitda")
        ev_ebitda_avg = bench.get("ev_ebitda_avg")
        if ev_ebitda is not None and ev_ebitda > 0 and ev_ebitda_avg is not None:
            count += 1
            ratio = ev_ebitda / ev_ebitda_avg
            if ratio < 0.6:
                points += 2.0
                score.strengths.append(
                    f"EV/EBITDA {ev_ebitda:.1f}x（業種平均{ev_ebitda_avg}xの{ratio:.0%}）→ 割安"
                )
            elif ratio < 0.85:
                points += 1.3
                score.strengths.append(f"EV/EBITDA {ev_ebitda:.1f}x（やや割安）")
            elif ratio < 1.2:
                points += 0.7
            else:
                points += 0.2
                if ratio > 1.5:
                    score.weaknesses.append(f"EV/EBITDA {ev_ebitda:.1f}x（業種平均比 割高）")

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

        # Altman Z-Score（倒産リスク評価）
        # 金融・不動産は財務構造が異なるためスキップ
        sector = fd.get("sector", "")
        if sector not in _SKIP_ALTMAN_Z_SECTORS:
            z, z_note = self._calc_altman_z(fd)
            if z is not None:
                score.altman_z = z
                count += 1
                if z >= 2.99:
                    points += 2.0
                    score.strengths.append(f"Altman Z-Score {z:.2f}（安全圏 ≥2.99）→ 倒産リスク低")
                elif z >= 1.81:
                    points += 1.0
                    score.warnings.append(f"Altman Z-Score {z:.2f}（グレーゾーン 1.81〜2.99）")
                else:
                    points += 0.0
                    score.warnings.append(f"Altman Z-Score {z:.2f}（警戒圏 <1.81）→ 財務リスクあり")
                if z_note:
                    score.warnings.append(z_note)

        return round(points / max(count, 1), 2) if count > 0 else 0.5

    @staticmethod
    def _calc_altman_z(fd: dict) -> tuple[Optional[float], str]:
        """
        Altman Z-Score を計算する（修正版: 非製造業・新興国対応）

        Z = 6.56×X1 + 3.26×X2 + 6.72×X3 + 1.05×X4
        （修正 Altman Z': 上場・非製造業向け係数）

        X1 = 運転資本 / 総資産
        X2 = 利益剰余金 / 総資産
        X3 = EBIT / 総資産
        X4 = 自己資本簿価 / 総負債

        Returns:
            (z_score or None, warning_note)
        """
        total_assets     = fd.get("total_assets")
        working_capital  = fd.get("working_capital")
        retained_earnings= fd.get("retained_earnings")
        ebitda           = fd.get("ebitda")
        total_debt       = fd.get("total_debt_abs")
        market_cap       = fd.get("market_cap")
        operating_margin = fd.get("operating_margin")
        total_revenue    = fd.get("total_revenue")

        if not total_assets or total_assets <= 0:
            return None, ""

        # X1: 運転資本 / 総資産
        x1 = (working_capital / total_assets) if working_capital is not None else None

        # X2: 利益剰余金 / 総資産
        x2 = (retained_earnings / total_assets) if retained_earnings is not None else None

        # X3: EBIT / 総資産（EBITDAを近似値として使用）
        ebit_proxy = None
        if ebitda and ebitda > 0:
            ebit_proxy = ebitda  # 減価償却を引けないため過大評価だが方向性は正しい
        elif operating_margin and total_revenue:
            ebit_proxy = operating_margin * total_revenue
        x3 = (ebit_proxy / total_assets) if ebit_proxy is not None else None

        # X4: 時価総額 / 総負債（修正版: 簿価自己資本の代わりに時価総額を使用）
        x4 = None
        if total_debt and total_debt > 0 and market_cap:
            x4 = market_cap / total_debt
        elif total_debt == 0 and market_cap:
            x4 = 10.0  # 無借金企業は最高評価

        # 計算可能なコンポーネント数を確認
        components = [c for c in [x1, x2, x3, x4] if c is not None]
        if len(components) < 2:
            return None, ""

        # 欠損コンポーネントはゼロ補完（保守的評価）
        x1 = x1 or 0.0
        x2 = x2 or 0.0
        x3 = x3 or 0.0
        x4 = x4 or 0.0

        z = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4

        note = ""
        if len(components) < 4:
            note = f"Altman Z-Score: {4 - len(components)}項目がデータ不足のため近似値"

        return round(z, 2), note

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
