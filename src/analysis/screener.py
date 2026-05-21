"""
複合スクリーニングエンジン
================================
ファンダメンタル・テクニカル・マクロを統合し、
推奨候補銘柄を絞り込む。

スクリーニングの流れ:
  1. ハードフィルター（最低条件を満たさない銘柄を除外）
  2. ソフトスコアリング（複合スコアで順位付け）
  3. 推奨候補の選定（閾値以上のみ）

推奨方針:
  - 強い根拠が複数揃った銘柄のみを「推奨」とする
  - 根拠が弱い・リスクが高い場合は「要観察」または「推奨なし」
  - 「ほぼ確実」な銘柄は存在しないため、確度スコアを正直に提示する
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from src.analysis.fundamental import FundamentalScore
from src.analysis.technical import TechnicalSignal
from src.analysis.valuation import ValuationResult


@dataclass
class Candidate:
    """スクリーニング通過銘柄の評価結果（推奨レポートの基礎データ）"""
    symbol: str
    name: str
    sector: str
    market: str
    currency: str

    # 各分析結果
    fundamental: FundamentalScore
    technical: TechnicalSignal
    valuation: ValuationResult

    # 総合スコア（0〜10点）
    composite_score: float = 0.0

    # 推奨判定
    recommendation: str = "様子見"  # "強く推奨" / "推奨" / "要観察" / "様子見"
    confidence: str = "低"          # "高" / "中" / "低"

    # 推奨理由のサマリ
    bull_case: list[str] = field(default_factory=list)   # 上昇根拠
    bear_case: list[str] = field(default_factory=list)   # リスク・下落根拠
    key_risks: list[str] = field(default_factory=list)   # 重要リスク

    # 利確目標への到達見込み期間
    months_to_target: Optional[str] = None  # 例: "6〜12ヶ月"

    # スクリーニング通過フラグ
    passed_hard_filter: bool = True
    filter_rejection_reason: str = ""


class StockScreener:
    """
    複合スクリーニングクラス

    重み設定（ファンダを重視・テクニカルは補助）:
      ファンダメンタル : 55%
      バリュエーション : 25%
      テクニカル       : 20%
    """

    FUNDAMENTAL_WEIGHT  = 0.55
    VALUATION_WEIGHT    = 0.25
    TECHNICAL_WEIGHT    = 0.20

    # 推奨閾値
    STRONG_BUY_THRESHOLD = 7.5
    BUY_THRESHOLD        = 6.0
    WATCH_THRESHOLD      = 4.5

    def screen(
        self,
        fundamentals: dict[str, FundamentalScore],
        technicals:   dict[str, TechnicalSignal],
        valuations:   dict[str, ValuationResult],
        raw_fd:       dict[str, dict],
        macro_score:  float = 0.0,
    ) -> list[Candidate]:
        """
        全銘柄をスクリーニングして候補リストを返す

        Args:
            fundamentals : {symbol: FundamentalScore}
            technicals   : {symbol: TechnicalSignal}
            valuations   : {symbol: ValuationResult}
            raw_fd       : {symbol: raw fundamentals dict}
            macro_score  : マクロスコア（-2〜+2）
        Returns:
            Candidateリスト（スコア降順）
        """
        candidates = []
        all_symbols = set(fundamentals.keys())

        for symbol in all_symbols:
            fd_score = fundamentals.get(symbol)
            tech     = technicals.get(symbol)
            val      = valuations.get(symbol)
            fd_raw   = raw_fd.get(symbol, {})

            if not fd_score:
                continue

            # 基本情報
            candidate = Candidate(
                symbol=symbol,
                name=fd_score.name,
                sector=fd_raw.get("sector", "") if fd_score.data_quality != "unavailable" else "",
                market=fd_raw.get("market", ""),
                currency=fd_raw.get("currency", "USD"),
                fundamental=fd_score,
                technical=tech or TechnicalSignal(symbol=symbol),
                valuation=val or ValuationResult(symbol=symbol, current_price=None, currency="USD"),
            )

            # ハードフィルター
            reject_reason = self._hard_filter(fd_score, tech, fd_raw)
            if reject_reason:
                candidate.passed_hard_filter = False
                candidate.filter_rejection_reason = reject_reason
                continue   # スクリーニング除外

            # 複合スコア計算
            composite = self._calc_composite_score(fd_score, tech, val, macro_score)
            candidate.composite_score = composite

            # 推奨判定
            candidate.recommendation, candidate.confidence = self._judge_recommendation(
                composite, fd_score, tech, val
            )

            # 推奨根拠の構築
            self._build_case(candidate, fd_score, tech, val, fd_raw)

            candidates.append(candidate)

        # スコア降順にソート
        candidates.sort(key=lambda c: c.composite_score, reverse=True)
        logger.info(
            f"スクリーニング完了: {len(candidates)}/{len(all_symbols)} 銘柄通過 "
            f"（推奨: {sum(1 for c in candidates if '推奨' in c.recommendation)}銘柄）"
        )
        return candidates

    # ─── ハードフィルター（除外条件） ──────────────────────────────

    def _hard_filter(
        self,
        fd: FundamentalScore,
        tech: Optional[TechnicalSignal],
        fd_raw: dict,
    ) -> str:
        """
        最低条件を満たさない銘柄を除外する

        Returns:
            拒否理由（空文字 = 通過）
        """
        # データ品質が最低限必要
        if fd.data_quality == "unavailable":
            return "財務データ取得不可"

        # 著しく高いPERは投機的とみなす
        per = fd_raw.get("per_trailing") or fd_raw.get("per_forward")
        if per is not None and per > 150:
            return f"PER {per:.0f}倍超（投機的水準）"

        # ROEが著しくマイナス（構造的赤字）
        roe = fd_raw.get("roe")
        if roe is not None and roe < -0.20:
            return f"ROE {roe*100:.0f}%（深刻な収益悪化）"

        # FCFが大幅マイナス かつ 財務健全性スコアが低い
        fcf = fd_raw.get("free_cashflow")
        if fcf is not None and fcf < 0 and fd.health_score < 0.5:
            return "FCFマイナス＋財務健全性低"

        # 1ヶ月・3ヶ月リターンが両方マイナス → 明確な下降トレンド
        if tech is not None:
            t1m = tech.trend_1m
            t3m = tech.trend_3m
            if (t1m is not None and t1m < 0) and (t3m is not None and t3m < 0):
                return f"下降トレンド継続（1M: {t1m:.1f}%、3M: {t3m:.1f}%）"

        return ""  # 通過

    # ─── 複合スコア計算 ─────────────────────────────────────────

    def _calc_composite_score(
        self,
        fd:    FundamentalScore,
        tech:  Optional[TechnicalSignal],
        val:   Optional[ValuationResult],
        macro_score: float,
    ) -> float:
        """複合スコアを計算（0〜10点）"""

        # ファンダメンタルスコア（0〜10点 → 0〜10点に正規化済み）
        fd_score = fd.total_score

        # バリュエーションスコア（上昇余地があるほど高スコア）
        val_score = 5.0   # デフォルト中立
        if val and val.upside_pct is not None:
            upside = val.upside_pct
            if upside > 30:   val_score = 9.0
            elif upside > 20: val_score = 8.0
            elif upside > 10: val_score = 7.0
            elif upside > 0:  val_score = 6.0
            elif upside > -10: val_score = 4.0
            else:              val_score = 2.0

        # テクニカルスコア（0〜2点 → 0〜10点に変換）
        tech_score = (tech.tech_score * 5.0) if tech else 5.0

        # マクロ調整（-1〜+1点）
        macro_adj = max(-1.0, min(1.0, macro_score * 0.5))

        composite = (
            fd_score    * self.FUNDAMENTAL_WEIGHT +
            val_score   * self.VALUATION_WEIGHT   +
            tech_score  * self.TECHNICAL_WEIGHT
        ) + macro_adj

        return round(max(0.0, min(10.0, composite)), 2)

    # ─── 推奨判定 ────────────────────────────────────────────────

    def _judge_recommendation(
        self,
        score: float,
        fd:    FundamentalScore,
        tech:  Optional[TechnicalSignal],
        val:   Optional[ValuationResult],
    ) -> tuple[str, str]:
        """推奨ラベルと確度を決定"""

        # 基本判定
        if score >= self.STRONG_BUY_THRESHOLD:
            rec = "強く推奨"
        elif score >= self.BUY_THRESHOLD:
            rec = "推奨"
        elif score >= self.WATCH_THRESHOLD:
            rec = "要観察"
        else:
            rec = "様子見"

        # 確度（データ品質・根拠の厚さで決まる）
        bull_count = len(fd.strengths)
        bear_count = len(fd.weaknesses) + len(fd.warnings)

        if fd.data_quality == "full" and bull_count >= 4 and bear_count <= 1:
            confidence = "高"
        elif fd.data_quality != "unavailable" and bull_count >= 2:
            confidence = "中"
        else:
            confidence = "低"

        # データが不完全な場合は強推奨に昇格させない
        if fd.data_quality == "partial" and rec == "強く推奨":
            rec = "推奨"
            confidence = "中"

        # 理論株価中央値が現在株価を大きく下回る場合は確度を下げる
        if val and val.upside_pct is not None and val.upside_pct < -20 and val.analyst_target is None:
            if rec == "強く推奨":
                rec = "推奨"
            confidence = "低"

        # 上昇余地が小さすぎる場合はリスクリワードが合わないため格下げ
        if val and val.upside_pct is not None:
            if val.upside_pct < 5:
                # 残り上昇余地ほぼゼロ → 推奨不可
                rec = "要観察"
                confidence = "低"
            elif val.upside_pct < 10:
                # 上昇余地が限定的 → 強推奨は出さない
                if rec == "強く推奨":
                    rec = "推奨"
                if confidence == "高":
                    confidence = "中"

        return rec, confidence

    # ─── 推奨根拠 ────────────────────────────────────────────────

    def _build_case(
        self,
        candidate: Candidate,
        fd: FundamentalScore,
        tech: Optional[TechnicalSignal],
        val: Optional[ValuationResult],
        fd_raw: dict,
    ) -> None:
        """推奨根拠・リスクリストを構築"""

        # 強気根拠
        candidate.bull_case.extend(fd.strengths[:3])
        if tech:
            candidate.bull_case.extend([s for s in tech.signals if "過熱" not in s][:2])
        if val and val.upside_pct and val.upside_pct > 10:
            candidate.bull_case.append(
                f"理論株価まで +{val.upside_pct:.1f}%の上昇余地"
            )

        # リスク・弱気根拠
        candidate.bear_case.extend(fd.weaknesses[:2])
        candidate.key_risks.extend(fd.warnings[:2])

        # 自動リスク付記
        per = fd_raw.get("per_trailing")
        if per and per > 40:
            candidate.key_risks.append(f"高PER（{per:.0f}x）→ 決算ミスで急落リスク")

        roe = fd_raw.get("roe")
        if roe and roe < 0.05:
            candidate.key_risks.append(f"ROE低水準（{roe*100:.1f}%）→ 資本効率改善が課題")

        if tech and tech.rsi_14 and tech.rsi_14 > 70:
            candidate.key_risks.append(f"RSI {tech.rsi_14:.0f}（過熱気味）→ 短期調整リスク")

        # 急騰後かつ上昇余地が限定的な場合の警告
        if tech and tech.trend_1m and val and val.upside_pct is not None:
            if tech.trend_1m > 20 and val.upside_pct < 15:
                candidate.key_risks.append(
                    f"直近1ヶ月で急騰済み（+{tech.trend_1m:.1f}%）→ 上昇余地が限定的・高値掴みに注意"
                )

        next_earn = fd_raw.get("next_earnings_date")
        if next_earn:
            candidate.key_risks.append(f"次回決算: {next_earn}（決算跨ぎリスク）")

        # 利確目標への到達見込み期間
        candidate.months_to_target = self._estimate_months_to_target(tech, val)

    # ─── 利確目標 到達見込み ─────────────────────────────────────

    def _estimate_months_to_target(
        self,
        tech: Optional[TechnicalSignal],
        val:  Optional[ValuationResult],
    ) -> Optional[str]:
        """
        利確目標（take_profit）への到達見込み期間を推定する。

        計算方針:
          - 直近6ヶ月リターン（月次換算）を基準ペースとして使用
          - 6ヶ月がマイナスなら3ヶ月を参照
          - どちらもマイナスなら保守値（年率8%）を採用
          - 上昇余地 ÷ 月次ペース = 到達月数
        """
        if not val or not val.upside_pct or val.upside_pct <= 0:
            return None

        # 上昇余地が5%未満はすでに目標圏内とみなす
        if val.upside_pct < 5:
            return "すでに目標圏内（利確検討タイミング）"

        upside = val.upside_pct / 100  # 小数に変換

        # 月次ペースを推定（直近の実績から）
        monthly_pace: Optional[float] = None
        if tech:
            if tech.trend_6m and tech.trend_6m > 0:
                monthly_pace = tech.trend_6m / 6 / 100
            elif tech.trend_3m and tech.trend_3m > 0:
                monthly_pace = tech.trend_3m / 3 / 100

        if not monthly_pace or monthly_pace <= 0:
            # 保守値: 年率8%（長期株式市場平均）
            monthly_pace = 0.08 / 12

        months = upside / monthly_pace
        months = max(1.0, min(72.0, months))  # 1ヶ月〜6年の範囲でキャップ

        if months <= 3:
            return "3ヶ月以内"
        elif months <= 6:
            return "3〜6ヶ月"
        elif months <= 12:
            return "6〜12ヶ月"
        elif months <= 24:
            return "1〜2年"
        elif months <= 36:
            return "2〜3年"
        else:
            return "3年超（長期保有向き）"


_RECOMMENDED_LABELS = {"強く推奨", "推奨"}

def filter_recommendations(candidates: list[Candidate]) -> list[Candidate]:
    """「推奨」以上の銘柄のみを抽出（要観察・様子見は除外）"""
    return [c for c in candidates if c.recommendation in _RECOMMENDED_LABELS]
