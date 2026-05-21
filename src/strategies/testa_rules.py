"""
テスタ投資哲学コアルールの実装
テスタ氏の「負けないことを最優先」哲学をアルゴリズムに落とし込む

テスタ氏の主要原則:
1. 負けないことが最優先（損失回避最重要）
2. 損切りは素早く・確実に（躊躇なく）
3. 利益は伸ばす（トレーリングストップ活用）
4. 流動性の高い銘柄のみ（板の薄い銘柄は避ける）
5. トレンドに逆らわない（市場の流れに乗る）
6. 相場全体を見てから個別株を見る（マクロ→ミクロ）
7. 出来高で動きを確認する（出来高なき上昇は信用しない）
8. 感情を排除する（ルール通りに実行）
9. リスクリワード比を守る（最低1:2）
10. ポジションサイズを守る（1取引で資産を傾けない）
"""
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
import pandas as pd
from loguru import logger


@dataclass
class TestaCheckResult:
    """テスタルールチェックの結果"""
    passed: bool
    score: float          # 0〜100のルールスコア
    reasons: list         # 通過理由
    warnings: list        # 警告（取引可能だが注意が必要）
    blockers: list        # ブロック理由（取引不可）


class TestaRulesEngine:
    """
    テスタ氏の投資哲学に基づくルールエンジン
    全戦略の共通フィルターとして機能する
    """

    def __init__(self, settings: dict):
        self.rules = settings["testa_rules"]
        self.market_rules = self.rules["market_filter"]
        self.entry_rules = self.rules["entry"]

    def check_all_rules(
        self,
        symbol: str,
        df: pd.DataFrame,
        market_status: Dict,
        direction: str = "long",
    ) -> TestaCheckResult:
        """
        全テスタルールを検証して取引の適否を判断する
        direction: "long"（買い）または "short"（売り）
        """
        reasons = []
        warnings = []
        blockers = []
        score = 0.0

        # ルール1: 市場全体フィルター（最重要）
        market_ok, market_msg = self._check_market_condition(market_status)
        if not market_ok:
            blockers.append(market_msg)
        else:
            reasons.append(market_msg)
            score += 25

        # ルール2: 流動性チェック
        liquidity_ok, liquidity_msg = self._check_liquidity(df)
        if not liquidity_ok:
            blockers.append(liquidity_msg)
        else:
            reasons.append(liquidity_msg)
            score += 20

        # ルール3: トレンド方向チェック
        trend_ok, trend_msg = self._check_trend_alignment(df, market_status, direction)
        if not trend_ok:
            warnings.append(trend_msg)
            score -= 10
        else:
            reasons.append(trend_msg)
            score += 20

        # ルール4: 出来高確認（出来高なき上昇は信用しない）
        volume_ok, volume_msg = self._check_volume_confirmation(df)
        if not volume_ok:
            warnings.append(volume_msg)
        else:
            reasons.append(volume_msg)
            score += 15

        # ルール5: モメンタムチェック
        momentum_ok, momentum_msg = self._check_momentum(df, direction)
        if not momentum_ok:
            warnings.append(momentum_msg)
        else:
            reasons.append(momentum_msg)
            score += 10

        # ルール6: RSIが極端な値でないか
        rsi_ok, rsi_msg = self._check_rsi_level(df, direction)
        if not rsi_ok:
            blockers.append(rsi_msg)
        else:
            reasons.append(rsi_msg)
            score += 10

        passed = len(blockers) == 0 and score >= 40

        return TestaCheckResult(
            passed=passed,
            score=round(score, 1),
            reasons=reasons,
            warnings=warnings,
            blockers=blockers,
        )

    def _check_market_condition(self, market_status: Dict) -> Tuple[bool, str]:
        """
        ルール6: 相場全体を見てから個別株を見る
        VIXが高い時は取引しない
        """
        vix = market_status.get("vix")
        condition = market_status.get("market_condition", "unknown")
        vix_max = self.market_rules["vix_max"]

        if vix is None:
            return True, "VIX取得不可（デフォルト許可）"

        if vix > vix_max:
            return False, f"VIX={vix:.1f} > {vix_max}（極度の恐怖：取引停止）"

        if condition == "extreme_fear":
            return False, f"市場状態: 極度の恐怖（取引停止）"

        vix_caution = self.market_rules["vix_caution"]
        if vix > vix_caution:
            return True, f"VIX={vix:.1f}（注意：ポジション半減推奨）"

        return True, f"市場状態: {condition} / VIX={vix:.1f}（取引適切）"

    def _check_liquidity(self, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        ルール4: 流動性の高い銘柄のみ取引する
        出来高が少ない銘柄はスプレッドが広くコストが高い
        """
        if df is None or len(df) < 5:
            return False, "データ不足"

        recent_volume = df["volume"].tail(5).mean()
        min_volume = self.entry_rules["min_daily_volume"]

        if recent_volume < min_volume:
            return False, f"出来高不足: {recent_volume:,.0f} < {min_volume:,.0f}（流動性なし）"

        return True, f"流動性OK: 平均出来高 {recent_volume:,.0f}"

    def _check_trend_alignment(
        self,
        df: pd.DataFrame,
        market_status: Dict,
        direction: str
    ) -> Tuple[bool, str]:
        """
        ルール5: トレンドに逆らわない
        市場全体のトレンドと個別株のトレンドが一致しているか
        """
        if "ema20" not in df.columns or "ema50" not in df.columns:
            return True, "トレンド指標未計算"

        last = df.iloc[-1]
        close = last["close"]
        ema20 = last["ema20"]
        ema50 = last["ema50"]

        # 個別株のトレンド判断
        if direction == "long":
            stock_trend_ok = close > ema20 and ema20 > ema50
        else:
            stock_trend_ok = close < ema20 and ema20 < ema50

        # 市場全体のトレンド判断
        market_trend = market_status.get("spy_trend", "neutral")
        if direction == "long":
            market_ok = market_trend in ("up", "neutral")
        else:
            market_ok = market_trend in ("down", "neutral")

        if not stock_trend_ok:
            return False, f"株トレンド逆行: close={close:.2f}, EMA20={ema20:.2f}, EMA50={ema50:.2f}"

        if not market_ok:
            return False, f"市場トレンド逆行: SPY={market_trend}（{direction}と不一致）"

        return True, f"トレンド一致: 株・市場ともに{direction}方向"

    def _check_volume_confirmation(self, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        ルール7: 出来高で動きを確認する
        テスタ氏の「出来高なき上昇は信用しない」
        """
        if "volume_ratio" not in df.columns:
            return True, "出来高比率未計算"

        last = df.iloc[-1]
        volume_ratio = last.get("volume_ratio", 1.0)
        min_ratio = self.entry_rules["min_volume_ratio"]

        if volume_ratio < min_ratio:
            return False, f"出来高不足: {volume_ratio:.2f}x（最低{min_ratio}x必要）"

        return True, f"出来高確認OK: {volume_ratio:.2f}x（平均比）"

    def _check_momentum(self, df: pd.DataFrame, direction: str) -> Tuple[bool, str]:
        """
        ルール1: 強い銘柄を買う / 弱い銘柄は避ける
        モメンタムが方向性と一致しているか
        """
        if "momentum_5" not in df.columns:
            return True, "モメンタム未計算"

        last = df.iloc[-1]
        momentum = last.get("momentum_5", 0)

        if direction == "long" and momentum < 0:
            return False, f"5日モメンタム負（{momentum*100:.1f}%）: 弱い銘柄への逆張り"

        if direction == "short" and momentum > 0:
            return False, f"5日モメンタム正（{momentum*100:.1f}%）: 強い銘柄への逆張り"

        return True, f"モメンタム確認: {momentum*100:.1f}%（{direction}方向）"

    def _check_rsi_level(self, df: pd.DataFrame, direction: str) -> Tuple[bool, str]:
        """
        過熱感・売られ過ぎのチェック
        買いエントリーでRSI>75の過熱圏は避ける
        """
        if "rsi" not in df.columns:
            return True, "RSI未計算"

        last = df.iloc[-1]
        rsi = last.get("rsi", 50)

        if direction == "long" and rsi > 80:
            return False, f"RSI={rsi:.1f}: 買われ過ぎ圏（買いエントリー不可）"

        if direction == "short" and rsi < 20:
            return False, f"RSI={rsi:.1f}: 売られ過ぎ圏（売りエントリー不可）"

        if direction == "long" and rsi > 70:
            return True, f"RSI={rsi:.1f}: やや過熱気味（注意）"

        return True, f"RSI={rsi:.1f}: 正常範囲"

    def calculate_position_quality(self, check_result: TestaCheckResult) -> str:
        """
        チェック結果からポジション品質を判定する
        A: 最高品質（全ルール通過・高スコア）
        B: 良質（主要ルール通過・警告あり）
        C: 要注意（通過だが条件不十分）
        F: 取引不可
        """
        if not check_result.passed:
            return "F"
        if check_result.score >= 80 and not check_result.warnings:
            return "A"
        elif check_result.score >= 60:
            return "B"
        else:
            return "C"
