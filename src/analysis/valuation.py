"""
理論株価計算モジュール
================================
複数の手法で理論株価・適正株価レンジを算出する。

採用する計算手法:
  1. PER倍率法   : EPS × 適正PER（セクター平均）
  2. グレアム公式: √(22.5 × EPS × BPS)
  3. DCF法（簡易）: FCF / (割引率 - 成長率)
  4. アナリスト  : yfinanceから取得したコンセンサス目標株価

注意事項:
  - 理論株価はあくまで「参考値」であり、将来株価の保証ではない
  - 各手法で前提が異なるため、複数手法の中央値・レンジで判断する
  - 成長率・割引率の仮定が結果に大きく影響する
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.analysis.fundamental import SECTOR_BENCHMARKS, DEFAULT_BENCHMARK


@dataclass
class ValuationResult:
    """理論株価計算結果"""
    symbol: str
    current_price: Optional[float]
    currency: str

    # 各手法の理論株価
    per_valuation: Optional[float] = None     # PER倍率法
    graham_value:  Optional[float] = None     # グレアム公式
    dcf_value:     Optional[float] = None     # DCF法
    analyst_target:Optional[float] = None     # アナリストコンセンサス

    # 総合理論株価レンジ
    fair_value_low:  Optional[float] = None
    fair_value_mid:  Optional[float] = None
    fair_value_high: Optional[float] = None

    # 上昇余地・下落余地
    upside_pct:   Optional[float] = None   # (fair_mid - current) / current
    downside_pct: Optional[float] = None   # 損切り水準まで

    # 損切り・利確目安
    stop_loss:    Optional[float] = None
    take_profit:  Optional[float] = None

    # 計算の根拠・注釈
    method_notes: list[str] = field(default_factory=list)
    warnings:     list[str] = field(default_factory=list)

    @property
    def upside_label(self) -> str:
        if self.upside_pct is None:
            return "N/A"
        sign = "+" if self.upside_pct >= 0 else ""
        return f"{sign}{self.upside_pct:.1f}%"

    @property
    def price_display(self) -> str:
        """通貨記号付きの現在株価表示"""
        if self.current_price is None:
            return "N/A"
        sym = "¥" if self.currency == "JPY" else "$"
        return f"{sym}{self.current_price:,.0f}" if self.currency == "JPY" else f"{sym}{self.current_price:.2f}"


class ValuationCalculator:
    """
    理論株価計算クラス

    DCFパラメータ（保守的設定）:
      割引率 8%（日本株・米国株共通の保守的WACC想定）
      ターミナル成長率 2.5%（長期インフレ率相当）
      FCF成長期間 5年
    """

    DISCOUNT_RATE = 0.08        # 割引率（WACC想定）
    TERMINAL_GROWTH = 0.025     # ターミナル成長率
    GROWTH_YEARS = 5            # 高成長期間（年）

    # 損切り水準（ATRが取得できない場合のデフォルト）
    DEFAULT_STOP_LOSS_PCT = 0.08   # 現値から -8%
    DEFAULT_TAKE_PROFIT_PCT = 0.15  # 現値から +15%

    def calculate(self, fd: dict) -> ValuationResult:
        """
        財務データから理論株価を計算する

        Args:
            fd: StockFetcher.fetch_fundamentals() の返り値
        Returns:
            ValuationResult
        """
        symbol   = fd.get("symbol", "")
        current  = fd.get("current_price")
        currency = fd.get("currency", "USD")
        sector   = fd.get("sector", "")
        bench    = SECTOR_BENCHMARKS.get(sector, DEFAULT_BENCHMARK)

        result = ValuationResult(
            symbol=symbol,
            current_price=current,
            currency=currency,
        )

        if current is None or current <= 0:
            result.warnings.append("現在株価が取得できないため理論株価計算不可")
            return result

        fair_values = []

        # ① PER倍率法
        per_val = self._per_method(fd, bench, result)
        if per_val:
            result.per_valuation = per_val
            fair_values.append(per_val)

        # ② グレアム公式（ROEが30%超のアセットライト企業は除外：過小評価するため）
        roe = fd.get("roe") or 0
        if roe < 0.30:
            graham = self._graham_method(fd, result)
            if graham:
                result.graham_value = graham
                fair_values.append(graham)
        else:
            result.method_notes.append(
                f"グレアム公式: ROE{roe*100:.0f}%超のため除外（アセットライト企業では過小評価）"
            )

        # ③ DCF法（簡易）
        dcf = self._dcf_method(fd, result)
        if dcf:
            result.dcf_value = dcf
            fair_values.append(dcf)

        # ④ アナリスト目標株価
        target        = fd.get("target_mean_price") or fd.get("target_median_price")
        analyst_count = fd.get("analyst_count") or 0  # 以降の利確目標計算でも使用
        if target and analyst_count >= 3:
            result.analyst_target = target
            fair_values.append(target)
            result.method_notes.append(
                f"アナリストコンセンサス目標株価: {self._fmt(target, currency)}"
                f"（{analyst_count}名評価）"
            )

        # 理論株価レンジ計算
        if fair_values:
            sorted_vals = sorted(fair_values)
            n = len(sorted_vals)
            # 中央値: 外れ値に強く、単一手法のブレに引きずられない
            if n % 2 == 1:
                mid = sorted_vals[n // 2]
            else:
                mid = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2

            result.fair_value_low  = round(sorted_vals[0], 0)
            result.fair_value_mid  = round(mid, 0)
            result.fair_value_high = round(sorted_vals[-1], 0)
            result.upside_pct = round(
                (result.fair_value_mid - current) / current * 100, 1
            )
        else:
            result.warnings.append("理論株価計算に必要なデータ不足（EPS・BPS等）")

        # 損切りライン
        result.stop_loss    = round(current * (1 - self.DEFAULT_STOP_LOSS_PCT), 0)
        result.downside_pct = round(-self.DEFAULT_STOP_LOSS_PCT * 100, 1)

        # 利確目標: アナリスト目標株価 > 現在株価 → それを使用
        #           それ以外は理論株価中央値（現在株価より高い場合のみ）
        #           いずれも現在株価以下の場合は None（意味のない目標は表示しない）
        if result.analyst_target and result.analyst_target > current and analyst_count >= 3:
            result.take_profit = round(result.analyst_target, 0)
        elif result.fair_value_mid and result.fair_value_mid > current:
            result.take_profit = round(result.fair_value_mid, 0)
        else:
            result.take_profit = None  # 上昇余地なし → 利確目標を設定しない

        return result

    # ─── 各手法 ──────────────────────────────────────────────────

    def _per_method(self, fd: dict, bench: dict, result: ValuationResult) -> Optional[float]:
        """PER倍率法: EPS × 適正PER"""
        eps = fd.get("eps_ttm") or fd.get("eps_forward")
        if eps is None or eps <= 0:
            return None

        per_target = bench["per_avg"]
        value = eps * per_target

        # 株価がマイナス収益（EPS<0）は使用不可
        if value <= 0:
            result.warnings.append("EPSが負のためPER倍率法を使用不可")
            return None

        result.method_notes.append(
            f"PER倍率法: EPS {self._fmt(eps, fd.get('currency','USD'))} × "
            f"適正PER {per_target}x = {self._fmt(value, fd.get('currency','USD'))}"
        )
        return value

    def _graham_method(self, fd: dict, result: ValuationResult) -> Optional[float]:
        """
        ベンジャミン・グレアム公式
        Fair Value = √(22.5 × EPS × BPS)

        前提: EPS > 0 かつ BPS > 0
        22.5 = 適正PER 15 × 適正PBR 1.5
        """
        eps = fd.get("eps_ttm")
        bps = fd.get("bps")
        if eps is None or bps is None:
            return None
        if eps <= 0 or bps <= 0:
            return None

        value = (22.5 * eps * bps) ** 0.5
        result.method_notes.append(
            f"グレアム公式: √(22.5 × EPS{eps:.2f} × BPS{bps:.2f}) = "
            f"{self._fmt(value, fd.get('currency','USD'))}"
        )
        return value

    def _dcf_method(self, fd: dict, result: ValuationResult) -> Optional[float]:
        """
        DCF法（簡易版）
        FCFベースの現在価値を株式数で割って1株あたり価値を算出

        FCF成長シナリオ:
          - 最初5年: 直近成長率（上限20%、下限0%）
          - 以降: ターミナル成長率（2.5%）
        """
        fcf = fd.get("free_cashflow")
        market_cap = fd.get("market_cap")
        current = fd.get("current_price")

        if not all([fcf, market_cap, current]):
            return None
        if fcf <= 0 or market_cap <= 0 or current <= 0:
            return None

        # 暗示株式数 ≈ 時価総額 / 現在株価
        shares_approx = market_cap / current

        # 成長率（保守的に制限）
        rev_g = fd.get("revenue_growth") or 0.05
        fcf_growth = min(max(rev_g, 0.0), 0.20)  # 0%〜20%に制限

        r = self.DISCOUNT_RATE
        g_terminal = self.TERMINAL_GROWTH

        # 5年間のFCF現在価値
        pv_sum = 0.0
        fcf_t = fcf
        for t in range(1, self.GROWTH_YEARS + 1):
            fcf_t = fcf_t * (1 + fcf_growth)
            pv_sum += fcf_t / ((1 + r) ** t)

        # ターミナルバリュー（Gordon Growth Model）
        if r <= g_terminal:
            result.warnings.append("DCF: 割引率≤成長率のため計算スキップ")
            return None

        terminal_fcf = fcf_t * (1 + g_terminal)
        terminal_value = terminal_fcf / (r - g_terminal)
        pv_terminal = terminal_value / ((1 + r) ** self.GROWTH_YEARS)

        total_pv = pv_sum + pv_terminal
        per_share_value = total_pv / shares_approx

        # 異常値チェック: 現在株価の0.2倍未満 or 10倍超は信頼性なしとして除外
        if per_share_value <= 0:
            return None
        if current > 0 and (per_share_value > current * 10 or per_share_value < current * 0.2):
            result.method_notes.append(
                f"DCF法: 計算結果 {self._fmt(per_share_value, fd.get('currency','USD'))} → "
                f"現在株価比{per_share_value/current:.0f}倍のため除外（株式分割等のデータ不整合の可能性）"
            )
            return None

        result.method_notes.append(
            f"DCF法: FCF成長率{fcf_growth*100:.0f}%×{self.GROWTH_YEARS}年, "
            f"割引率{r*100:.0f}%, ターミナル成長率{g_terminal*100:.1f}% → "
            f"1株価値 {self._fmt(per_share_value, fd.get('currency','USD'))}"
        )
        return per_share_value

    # ─── フォーマット ─────────────────────────────────────────────

    @staticmethod
    def _fmt(value: float, currency: str) -> str:
        if currency == "JPY":
            return f"¥{value:,.0f}"
        return f"${value:.2f}"


def batch_valuate(fundamentals_dict: dict[str, dict]) -> dict[str, ValuationResult]:
    """複数銘柄の理論株価を一括計算"""
    calc = ValuationCalculator()
    return {symbol: calc.calculate(fd) for symbol, fd in fundamentals_dict.items()}
