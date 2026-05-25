"""
短期モメンタムスクリーナー（1〜2週間目線）
==========================================
4種のシグナルを100点満点で採点し、35点以上を候補として返す。

スコア構成:
  A. PEAD（決算後モメンタム）      : 0〜30点  ※有効期間を30日に延長
  B. 52週高値ブレイクアウト        : 0〜35点
  C. 短期モメンタム（5日・20日）   : 0〜15点  ※常時機能・決算タイミング非依存
  D. ニュースセンチメント（MarketAux）: 0〜15点
  E. ショートスクイーズ候補        : 0〜 5点
  合計: 100点満点、閾値: 35点

設計思想:
  - PEAD は最も精度が高いが決算シーズン外はスコア0になる（エピソード的）
  - ブレイクアウト＋短期モメンタムは常時機能する連続的指標
  - 閾値35点 = 「ブレイクアウト良好（20pt）＋上昇中（10pt）＋センチメント中立（5pt）」で到達
  - 短期トレードの性質上「必ず当たる」シグナルは存在しない点に留意
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from src.data.short_term_fetcher import ShortTermRawData

PASS_THRESHOLD = 35  # 合計スコアの最低ライン


# ─── 結果データクラス ─────────────────────────────────────────────

@dataclass
class ShortTermResult:
    """短期スクリーニング結果（1銘柄）"""

    symbol: str
    name: str
    current_price: Optional[float] = None

    # スコア内訳
    score_pead:          int = 0   # 0〜30
    score_breakout:      int = 0   # 0〜35
    score_momentum:      int = 0   # 0〜15（5日・20日リターン）
    score_sentiment:     int = 0   # 0〜15
    score_short_squeeze: int = 0   # 0〜 5
    total_score:         int = 0   # 0〜100

    # 合否
    qualified: bool = False

    # シグナル概要
    signal_type:      str = ""   # "決算後モメンタム" / "ブレイクアウト" / "複合"
    signal_freshness: str = ""   # "⚡ 最高（0〜2日）" etc

    # 各指標の表示文字列
    earnings_info:          str = "不明"
    pead_volume_str:        str = "不明"
    rsi_str:                str = "不明"
    ma200_str:              str = "不明"
    high52w_str:            str = "不明"
    volume_str:             str = "不明"
    momentum_str:           str = "不明"
    sentiment_str:          str = "不明"
    short_str:              str = "—"

    # 各シグナルの詳細条件リスト（UIに箇条書き表示）
    pead_conditions:     list = field(default_factory=list)
    breakout_conditions: list = field(default_factory=list)
    momentum_conditions: list = field(default_factory=list)

    # エントリー目安
    entry_note:    str   = "終値付近でのエントリーが基本"
    stop_loss_pct: float = 0.05   # -5%
    target_pct:    float = 0.08   # +8%
    hold_days:     str   = "5〜10営業日"


# ─── スクリーナー ────────────────────────────────────────────────

class ShortTermScreener:
    """短期モメンタムスコアリングクラス"""

    # ── A. PEAD スコア（0〜30点・有効期間30日） ─────────────────────

    def _score_pead(self, raw: ShortTermRawData) -> tuple[int, list[str]]:
        score = 0
        conds: list[str] = []

        # ① 経過日数（30日まで有効・鮮度で重み付け）
        days = raw.days_since_earnings
        if days is None:
            conds.append("❓ 決算日不明")
        elif days <= 3:
            score += 12
            conds.append(f"✅ 決算{days}日後（最鮮度・ドリフト初期）")
        elif days <= 10:
            score += 8
            conds.append(f"✅ 決算{days}日後（鮮度高）")
        elif days <= 20:
            score += 4
            conds.append(f"△ 決算{days}日後（ドリフト中盤）")
        elif days <= 30:
            score += 2
            conds.append(f"△ 決算{days}日後（ドリフト末期）")
        else:
            conds.append(f"— 決算{days}日後（PEADウィンドウ外）")

        # ② EPS beat幅
        beat = raw.eps_beat_pct
        if beat is None:
            conds.append("❓ EPS beat 不明")
        elif beat >= 0.20:
            score += 12
            conds.append(f"✅ EPS beat +{beat*100:.0f}%（大幅超過）")
        elif beat >= 0.10:
            score += 8
            conds.append(f"✅ EPS beat +{beat*100:.0f}%")
        elif beat >= 0.03:
            score += 4
            conds.append(f"△ EPS beat +{beat*100:.0f}%（小幅超過）")
        elif beat >= 0:
            score += 1
            conds.append(f"△ EPS beat +{beat*100:.0f}%（ほぼ予想通り）")
        else:
            score -= 4
            conds.append(f"❌ EPS miss {beat*100:.0f}%（予想下回り）")

        # ③ 決算日出来高
        vol_r = raw.earnings_day_volume_ratio
        if vol_r is None:
            conds.append("❓ 決算日出来高 不明")
        elif vol_r >= 2.0:
            score += 6
            conds.append(f"✅ 決算日出来高 平均比 {vol_r:.1f}倍（機関買い示唆）")
        elif vol_r >= 1.5:
            score += 3
            conds.append(f"△ 決算日出来高 平均比 {vol_r:.1f}倍")
        else:
            conds.append(f"— 決算日出来高 平均比 {vol_r:.1f}倍")

        return max(0, min(30, score)), conds

    # ── C. 短期モメンタムスコア（0〜15点・常時機能） ────────────────

    def _score_momentum(self, raw: ShortTermRawData) -> tuple[int, list[str]]:
        """5日・20日リターンで短期上昇トレンドを測定する。決算タイミング非依存。"""
        score = 0
        conds: list[str] = []

        # 5日リターン（直近の勢い）
        r5 = raw.return_5d
        if r5 is None:
            conds.append("❓ 5日リターン 不明")
        elif r5 >= 0.07:
            score += 10
            conds.append(f"✅ 5日リターン +{r5*100:.1f}%（強いモメンタム）")
        elif r5 >= 0.03:
            score += 7
            conds.append(f"✅ 5日リターン +{r5*100:.1f}%")
        elif r5 >= 0.01:
            score += 4
            conds.append(f"△ 5日リターン +{r5*100:.1f}%（上昇中）")
        elif r5 >= -0.02:
            score += 1
            conds.append(f"△ 5日リターン {r5*100:.1f}%（横ばい）")
        else:
            conds.append(f"❌ 5日リターン {r5*100:.1f}%（下落中）")

        # 20日リターン（中期トレンド確認）
        r20 = raw.return_20d
        if r20 is None:
            conds.append("❓ 20日リターン 不明")
        elif r20 >= 0.10:
            score += 5
            conds.append(f"✅ 20日リターン +{r20*100:.1f}%（中期上昇トレンド）")
        elif r20 >= 0.04:
            score += 3
            conds.append(f"△ 20日リターン +{r20*100:.1f}%")
        elif r20 < -0.05:
            score -= 3
            conds.append(f"❌ 20日リターン {r20*100:.1f}%（中期下落）")
        else:
            conds.append(f"— 20日リターン {r20*100:.1f}%")

        return max(0, min(15, score)), conds

    # ── B. ブレイクアウトスコア（0〜35点） ─────────────────────────

    def _score_breakout(self, raw: ShortTermRawData) -> tuple[int, list[str]]:
        score = 0
        conds: list[str] = []

        # ① 52週高値との距離
        p52 = raw.pct_from_52w_high
        if p52 is None:
            conds.append("❓ 52週高値データ不明")
        elif p52 >= -0.02:
            score += 15
            conds.append(f"✅ 52週高値まで {p52*100:.1f}%（ブレイク直前/直後）")
        elif p52 >= -0.05:
            score += 8
            conds.append(f"△ 52週高値まで {p52*100:.1f}%")
        elif p52 >= -0.10:
            score += 3
            conds.append(f"△ 52週高値まで {p52*100:.1f}%（やや遠い）")
        else:
            conds.append(f"❌ 52週高値まで {p52*100:.1f}%（遠い）")

        # ② 直近出来高（ブレイクの信頼性確認）
        vol_r = raw.volume_ratio_10d
        if vol_r is None:
            conds.append("❓ 出来高比 不明")
        elif vol_r >= 2.0:
            score += 10
            conds.append(f"✅ 出来高急増（10日平均比 {vol_r:.1f}倍）")
        elif vol_r >= 1.5:
            score += 6
            conds.append(f"△ 出来高増加（10日平均比 {vol_r:.1f}倍）")
        elif vol_r >= 1.0:
            score += 2
            conds.append(f"△ 出来高 平均並み（{vol_r:.1f}倍）")
        else:
            conds.append(f"❌ 出来高低調（{vol_r:.1f}倍）")

        # ③ 200日MA上方（トレンド確認）
        if raw.above_ma200 is True:
            score += 7
            conds.append("✅ 200日移動平均線 上方")
        elif raw.above_ma200 is False:
            conds.append("❌ 200日移動平均線 下方（下降トレンド）")
        else:
            conds.append("❓ 200日MA 不明")

        # ④ RSI（過熱感チェック）
        rsi = raw.rsi_14
        if rsi is None:
            conds.append("❓ RSI 不明")
        elif rsi < 70:
            score += 3
            conds.append(f"✅ RSI {rsi:.0f}（買われすぎていない）")
        elif rsi < 80:
            conds.append(f"△ RSI {rsi:.0f}（やや過熱）")
        else:
            score -= 5
            conds.append(f"❌ RSI {rsi:.0f}（過熱・買われすぎ）")

        return max(0, min(35, score)), conds

    # ── D. センチメントスコア（0〜15点） ───────────────────────────

    def _score_sentiment(self, score: Optional[float]) -> tuple[int, str]:
        if score is None:
            return 5, "不明（記事なし・中立扱い）"
        if score >= 0.5:
            return 15, f"強気 ({score:+.2f})"
        if score >= 0.2:
            return 10, f"やや強気 ({score:+.2f})"
        if score >= -0.2:
            return 5, f"中立 ({score:+.2f})"
        if score >= -0.5:
            return 0, f"やや弱気 ({score:+.2f})"
        return 0, f"弱気 ({score:+.2f})"

    # ── D. ショートスクイーズスコア（0〜5点） ──────────────────────

    def _score_short_squeeze(self, short_pct: Optional[float]) -> tuple[int, str]:
        if short_pct is None:
            return 0, "—"
        if short_pct >= 0.20:
            return 5, f"ショート残 {short_pct*100:.0f}%（高・スクイーズ警戒）"
        if short_pct >= 0.10:
            return 3, f"ショート残 {short_pct*100:.0f}%（中）"
        return 0, f"ショート残 {short_pct*100:.0f}%（低）"

    # ── 1銘柄評価 ──────────────────────────────────────────────────

    def evaluate(
        self,
        raw: ShortTermRawData,
        sentiment_score: Optional[float],
    ) -> ShortTermResult:
        r = ShortTermResult(
            symbol=raw.symbol,
            name=raw.name,
            current_price=raw.current_price,
        )

        # スコア計算
        r.score_pead,          r.pead_conditions     = self._score_pead(raw)
        r.score_breakout,      r.breakout_conditions = self._score_breakout(raw)
        r.score_momentum,      r.momentum_conditions = self._score_momentum(raw)
        r.score_sentiment,     r.sentiment_str       = self._score_sentiment(sentiment_score)
        r.score_short_squeeze, r.short_str           = self._score_short_squeeze(
            raw.short_percent_of_float
        )

        r.total_score = max(0, min(100,
            r.score_pead + r.score_breakout + r.score_momentum +
            r.score_sentiment + r.score_short_squeeze
        ))
        r.qualified = r.total_score >= PASS_THRESHOLD

        # シグナル種別
        if r.score_pead >= 20 and r.score_breakout >= 15:
            r.signal_type = "決算後ブレイクアウト（複合）"
        elif r.score_pead >= 20:
            r.signal_type = "決算後モメンタム（PEAD）"
        elif r.score_breakout >= 20:
            r.signal_type = "52週高値ブレイクアウト"
        else:
            r.signal_type = "モメンタム候補"

        # シグナル鮮度
        days = raw.days_since_earnings
        if days is not None and days <= 2:
            r.signal_freshness = "⚡ 最高（0〜2日）"
        elif days is not None and days <= 5:
            r.signal_freshness = "🔥 高（3〜5日）"
        elif days is not None and days <= 10:
            r.signal_freshness = "△ 中（6〜10日）"
        else:
            r.signal_freshness = "— 低/不明"

        # 表示文字列
        if raw.eps_beat_pct is not None and raw.last_earnings_date:
            r.earnings_info = (
                f"EPS beat {raw.eps_beat_pct*100:+.0f}%  ({raw.last_earnings_date})"
            )
        elif raw.last_earnings_date:
            r.earnings_info = raw.last_earnings_date

        r.pead_volume_str = (
            f"平均比 {raw.earnings_day_volume_ratio:.1f}倍"
            if raw.earnings_day_volume_ratio else "不明"
        )
        r.rsi_str     = f"{raw.rsi_14:.0f}" if raw.rsi_14 is not None else "不明"
        r.ma200_str   = (
            "上方" if raw.above_ma200 is True
            else "下方" if raw.above_ma200 is False
            else "不明"
        )
        r.high52w_str = (
            f"{raw.pct_from_52w_high*100:.1f}%"
            if raw.pct_from_52w_high is not None else "不明"
        )
        r.volume_str = (
            f"{raw.volume_ratio_10d:.1f}倍"
            if raw.volume_ratio_10d is not None else "不明"
        )

        parts = []
        if raw.return_5d is not None:
            parts.append(f"5日: {raw.return_5d*100:+.1f}%")
        if raw.return_20d is not None:
            parts.append(f"20日: {raw.return_20d*100:+.1f}%")
        r.momentum_str = " / ".join(parts) if parts else "不明"

        # エントリー目安
        if r.score_pead >= 25 and r.score_breakout >= 15:
            r.hold_days     = "5〜10営業日"
            r.target_pct    = 0.10
            r.stop_loss_pct = 0.05
        elif r.score_pead >= 20:
            r.hold_days     = "5〜10営業日"
            r.target_pct    = 0.08
            r.stop_loss_pct = 0.05
        elif r.score_breakout >= 20:
            r.hold_days     = "7〜15営業日"
            r.target_pct    = 0.08
            r.stop_loss_pct = 0.06

        return r

    # ── 全銘柄スクリーニング ────────────────────────────────────────

    def screen(
        self,
        raw_dict: dict[str, ShortTermRawData],
        sentiment_dict: dict[str, float],
    ) -> list[ShortTermResult]:
        """
        全銘柄をスコアリングし、50点以上をスコア降順で返す。

        Args:
            raw_dict:       {symbol: ShortTermRawData}
            sentiment_dict: {symbol: sentiment_score}（なければ中立扱い）
        """
        results = []
        for symbol, raw in raw_dict.items():
            sentiment = sentiment_dict.get(symbol)
            result    = self.evaluate(raw, sentiment)
            if result.qualified:
                results.append(result)

        logger.info(
            f"短期スクリーニング: {len(raw_dict)}銘柄 → {len(results)}銘柄が{PASS_THRESHOLD}点以上"
        )
        return sorted(results, key=lambda x: x.total_score, reverse=True)
