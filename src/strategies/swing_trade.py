"""
スイングトレード戦略
数日〜1週間の保有を前提とした戦略
初期資金が少ない場合はこちらがメイン戦略

テスタ哲学に基づく3つのスイング戦略:
1. MAクロスオーバー: EMA20がEMA50を上抜けでエントリー
2. ブレイクアウト: 20日高値を出来高増加で上抜けでエントリー
3. RSI平均回帰: トレンド中の一時的な押し目を狙う
"""
from typing import Optional, Dict
import pandas as pd
from loguru import logger

from .base_strategy import BaseStrategy, TradeSignal, SignalType
from .testa_rules import TestaRulesEngine


class SwingTradeStrategy(BaseStrategy):
    """スイングトレード戦略（デイトレ制限がある場合のメイン戦略）"""

    def __init__(self, settings: dict):
        super().__init__(settings)
        self.swing_config = settings["swing_trade"]
        self.testa_engine = TestaRulesEngine(settings)
        self.ma_config = self.swing_config["ma_crossover"]
        self.breakout_config = self.swing_config["breakout"]
        self.rsi_config = self.swing_config["rsi_reversion"]
        self.pullback_config = self.swing_config.get("ema_pullback", {})

    @property
    def name(self) -> str:
        return "SwingTrade"

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        market_status: Dict
    ) -> Optional[TradeSignal]:
        """
        スイングトレードのシグナルを生成する
        3つの戦略を順にチェックし、最初に条件を満たした戦略でシグナルを出す
        """
        if not self.check_market_condition(market_status):
            vix = market_status.get("vix", "?")
            cond = market_status.get("market_condition", "?")
            logger.info(f"[{symbol}] スキップ: 市場環境不適 (VIX={vix}, condition={cond})")
            return None

        if df is None or len(df) < 55:
            return None

        last = df.iloc[-1]
        signals = []
        rejection_notes = []

        # 戦略1: MAクロスオーバー
        if self.ma_config["enabled"]:
            signal = self._ma_crossover_signal(symbol, df, market_status)
            if signal:
                signals.append(signal)
            else:
                # ゴールデンクロスが起きているかチェックして理由をログ
                if "ema20" in df.columns and "ema50" in df.columns:
                    prev = df.iloc[-2]
                    gc = prev["ema20"] <= prev["ema50"] and last["ema20"] > last["ema50"]
                    rejection_notes.append(
                        f"MAクロス: {'クロス未発生' if not gc else 'GC発生もルール不通過'} "
                        f"(EMA20={last['ema20']:.1f}/EMA50={last['ema50']:.1f})"
                    )

        # 戦略2: ブレイクアウト
        if self.breakout_config["enabled"]:
            signal = self._breakout_signal(symbol, df, market_status)
            if signal:
                signals.append(signal)
            else:
                if "high_20" in df.columns:
                    dist = (last["close"] - last["high_20"]) / last["high_20"] * 100
                    rejection_notes.append(f"Breakout: 20日高値まで{dist:+.1f}%")

        # 戦略3: RSI平均回帰（押し目買い）
        if self.rsi_config["enabled"]:
            signal = self._rsi_reversion_signal(symbol, df, market_status)
            if signal:
                signals.append(signal)
            else:
                rsi = last.get("rsi", "?")
                oversold = self.rsi_config["oversold_level"]
                rejection_notes.append(f"RSI: {rsi:.1f} (売られ過ぎ={oversold}未満に未到達)")

        # 戦略4: EMA押し目買い（上昇トレンド中にEMA20付近まで引き付ける）
        if self.pullback_config.get("enabled"):
            signal = self._ema_pullback_signal(symbol, df, market_status)
            if signal:
                signals.append(signal)
            else:
                if "ema20" in df.columns:
                    dist = (last["close"] - last["ema20"]) / last["ema20"] * 100
                    rsi = last.get("rsi", "?")
                    max_dist = self.pullback_config.get("max_distance_pct", 0.02) * 100
                    recent_high = df.iloc[-10:]["close"].max()
                    pb_pct = (recent_high - last["close"]) / recent_high * 100
                    rejection_notes.append(
                        f"Pullback: EMA20乖離{dist:+.1f}%(±{max_dist:.0f}%以内要), "
                        f"RSI={rsi:.1f}({self.pullback_config.get('min_rsi',35)}-{self.pullback_config.get('max_rsi',55)}), "
                        f"押し目={pb_pct:.1f}%(2.5%以上要)"
                    )

        if not signals:
            logger.info(f"[{symbol}] シグナルなし | " + " / ".join(rejection_notes))
            return None

        # 複数シグナルがある場合は確信度が最も高いものを選ぶ
        best_signal = max(signals, key=lambda s: s.confidence)

        # テスタルール最終検証
        if not self.validate_signal(best_signal):
            logger.info(
                f"[{symbol}] テスタルール不通過: {best_signal.strategy_name} | "
                f"RR比={best_signal.risk_reward_ratio:.2f}(最低{self.min_rr_ratio}), "
                f"損切り幅={(best_signal.price - best_signal.stop_loss)/best_signal.price*100:.2f}%, "
                f"確信度={best_signal.confidence:.2f}"
            )
            return None

        logger.info(f"[{symbol}] ✅ {best_signal.strategy_name} シグナル: ${best_signal.price:.2f} | {best_signal.reason}")
        return best_signal

    def _ma_crossover_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        market_status: Dict
    ) -> Optional[TradeSignal]:
        """
        EMA20 × EMA50 クロスオーバー戦略
        テスタ氏の「トレンドフォロー」原則に基づく
        """
        if "ema20" not in df.columns or "ema50" not in df.columns:
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # ゴールデンクロス（EMA20がEMA50を上抜け）
        golden_cross = (
            prev["ema20"] <= prev["ema50"] and
            last["ema20"] > last["ema50"]
        )

        # 追加条件: 200日SMAより上（大きなトレンドが上昇中）
        # NaNはNumPy上で全比較がFalseになるため、NaNの場合はチェックスキップ（データ不足時の保護）
        sma200 = last.get("sma200")
        if sma200 is None or pd.isna(sma200):
            above_sma200 = True  # データ不足時はEMA50チェックに任せる
        else:
            above_sma200 = last["close"] > sma200

        if not golden_cross or not above_sma200:
            return None

        # テスタルール確認
        check = self.testa_engine.check_all_rules(symbol, df, market_status, "long")
        if not check.passed:
            return None

        price = last["close"]
        atr = last.get("atr", price * 0.02)
        stop_loss = self.calculate_stop_loss(price, atr, "long")
        # 損切り幅が上限を超える場合は上限に合わせる（高ボラ銘柄対応）
        max_stop_pct = self.testa_rules["stop_loss"]["max_pct"]
        stop_loss = max(stop_loss, price * (1 - max_stop_pct))
        take_profit = self.calculate_take_profit(price, stop_loss, "long")

        confidence = 0.65
        if last.get("volume_ratio", 1.0) > 1.5:
            confidence += 0.1
        if above_sma200:
            confidence += 0.05

        reason = (
            f"MAクロス買いシグナル: EMA20({last['ema20']:.2f}) > EMA50({last['ema50']:.2f}) "
            f"| スコア: {check.score}"
        )

        return TradeSignal(
            symbol=symbol,
            signal_type=SignalType.BUY,
            strategy_name="SwingMA",
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=min(confidence, 0.9),
            reason=reason,
            metadata={"testa_score": check.score, "strategy_sub": "ma_crossover"},
        )

    def _breakout_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        market_status: Dict
    ) -> Optional[TradeSignal]:
        """
        20日高値ブレイクアウト戦略
        テスタ氏の「高値更新を追え」原則に基づく
        """
        if "high_20" not in df.columns:
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # 前日は20日高値以下、当日は超えた
        breakout = (
            prev["close"] <= prev["high_20"] and
            last["close"] > last["high_20"] * 1.002  # 0.2%バッファ
        )

        # 出来高が平均の1.5倍以上（フェイクブレイクアウト排除）
        volume_confirmed = last.get("volume_ratio", 1.0) >= self.breakout_config["volume_confirm_ratio"]

        if not breakout or not volume_confirmed:
            return None

        # テスタルール確認
        check = self.testa_engine.check_all_rules(symbol, df, market_status, "long")
        if not check.passed:
            return None

        price = last["close"]
        atr = last.get("atr", price * 0.02)
        # ブレイクアウト戦略ではストップをブレイクポイント直下に設定
        max_stop_pct = self.testa_rules["stop_loss"]["max_pct"]
        stop_loss = round(last["high_20"] * 0.98, 2)
        stop_loss = max(stop_loss, self.calculate_stop_loss(price, atr, "long"))
        stop_loss = max(stop_loss, price * (1 - max_stop_pct))  # 上限以内に収める
        take_profit = self.calculate_take_profit(price, stop_loss, "long")

        confidence = 0.70
        if last.get("volume_ratio", 1.0) > 2.0:
            confidence += 0.1

        reason = (
            f"ブレイクアウト買いシグナル: ${last['close']:.2f} > 20日高値${last['high_20']:.2f} "
            f"| 出来高{last.get('volume_ratio', 1.0):.1f}x"
        )

        return TradeSignal(
            symbol=symbol,
            signal_type=SignalType.BUY,
            strategy_name="SwingBreakout",
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=min(confidence, 0.9),
            reason=reason,
            metadata={"testa_score": check.score, "strategy_sub": "breakout"},
        )

    def _rsi_reversion_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        market_status: Dict
    ) -> Optional[TradeSignal]:
        """
        RSI押し目買い戦略（平均回帰）
        上昇トレンド中の一時的な売られ過ぎを狙う
        """
        if "rsi" not in df.columns or "ema50" not in df.columns:
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # 大きなトレンドは上昇中（EMA50より上）
        in_uptrend = last["close"] > last["ema50"]

        # RSIが売られ過ぎ水準から回復中
        oversold_level = self.rsi_config["oversold_level"]
        rsi_recovering = (
            prev["rsi"] < oversold_level and
            last["rsi"] >= oversold_level
        )

        if not in_uptrend or not rsi_recovering:
            return None

        # テスタルール確認（トレンド確認は緩める）
        check = self.testa_engine.check_all_rules(symbol, df, market_status, "long")
        if not check.passed:
            return None

        price = last["close"]
        atr = last.get("atr", price * 0.02)
        max_stop_pct = self.testa_rules["stop_loss"]["max_pct"]
        stop_loss = self.calculate_stop_loss(price, atr, "long")
        stop_loss = max(stop_loss, price * (1 - max_stop_pct))  # 上限以内に収める
        take_profit = self.calculate_take_profit(price, stop_loss, "long")

        confidence = 0.62
        if last["close"] > last.get("ema20", 0):
            confidence += 0.08

        reason = (
            f"RSI押し目買いシグナル: RSI {prev['rsi']:.1f}→{last['rsi']:.1f} "
            f"（売られ過ぎ回復）| 上昇トレンド中"
        )

        return TradeSignal(
            symbol=symbol,
            signal_type=SignalType.BUY,
            strategy_name="SwingRSI",
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=min(confidence, 0.85),
            reason=reason,
            metadata={"testa_score": check.score, "strategy_sub": "rsi_reversion"},
        )

    def _ema_pullback_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        market_status: Dict
    ) -> Optional[TradeSignal]:
        """
        EMA押し目買い戦略（上昇トレンド継続中の押し目エントリー）
        テスタ氏の「強い株が一時的に下がったところを拾う」原則

        条件:
        - 上昇トレンド確認（EMA20 > EMA50 かつ 価格 > EMA50）
        - 価格がEMA20付近まで押し戻された（乖離2%以内）
        - RSIが35〜55（売られ過ぎでも買われ過ぎでもない中立ゾーン）
        - 前日より今日の価格が高い（トレンド継続の確認）
        """
        if "ema20" not in df.columns or "ema50" not in df.columns or "rsi" not in df.columns:
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]

        price = last["close"]
        ema20 = last["ema20"]
        ema50 = last["ema50"]
        rsi = last["rsi"]

        # 上昇トレンド確認
        in_uptrend = (ema20 > ema50) and (price > ema50)
        if not in_uptrend:
            return None

        # EMA20との距離計算
        max_dist = self.pullback_config.get("max_distance_pct", 0.02)
        distance_to_ema20 = (price - ema20) / ema20

        # RSIが適切な水準
        min_rsi = self.pullback_config.get("min_rsi", 35)
        max_rsi = self.pullback_config.get("max_rsi", 55)
        if not (min_rsi <= rsi <= max_rsi):
            return None

        # 直近10日の高値から2.5%以上押し戻されていること（押し目の確認）
        # ※旧閾値3%では有効シグナルが60分以内に失効するケースが多発したため緩和
        recent_high = df.iloc[-10:]["close"].max()
        pullback_pct = (recent_high - price) / recent_high
        if pullback_pct < 0.025:
            return None

        # EMA20との距離を緩和（上下2%以内）
        if not (-max_dist <= distance_to_ema20 <= max_dist):
            return None

        # 市場全体が上昇トレンドであること
        if market_status.get("spy_trend") not in ("up", "neutral"):
            return None

        atr = last.get("atr", price * 0.02)
        max_stop_pct = self.testa_rules["stop_loss"]["max_pct"]
        # ストップはEMA50直下に設定（トレンド転換点）
        stop_loss = round(ema50 * 0.99, 2)
        stop_loss = max(stop_loss, price * (1 - max_stop_pct))
        stop_loss = max(stop_loss, self.calculate_stop_loss(price, atr, "long"))
        take_profit = self.calculate_take_profit(price, stop_loss, "long")

        confidence = 0.68
        if last.get("volume_ratio", 1.0) > 1.2:
            confidence += 0.05
        sma200_val = last.get("sma200")
        if sma200_val is not None and not pd.isna(sma200_val) and price > sma200_val:
            confidence += 0.05

        reason = (
            f"EMA押し目買い: ${price:.2f} ≈ EMA20${ema20:.2f} "
            f"(乖離{distance_to_ema20*100:.1f}%) | RSI:{rsi:.0f} | 上昇トレンド継続"
        )

        return TradeSignal(
            symbol=symbol,
            signal_type=SignalType.BUY,
            strategy_name="SwingPullback",
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=min(confidence, 0.85),
            reason=reason,
            metadata={"strategy_sub": "ema_pullback"},
        )

    def should_exit(
        self,
        symbol: str,
        entry_price: float,
        current_price: float,
        stop_loss: float,
        take_profit: float,
        hold_days: int,
        df: pd.DataFrame
    ) -> Optional[TradeSignal]:
        """
        スイングポジションのエグジット条件を確認する
        テスタ氏の「損切りは素早く・利益は伸ばす」
        """
        # ストップロス到達（最優先）
        if current_price <= stop_loss:
            reason = f"損切りライン到達: ${current_price:.2f} <= ${stop_loss:.2f}"
            logger.warning(f"[{symbol}] {reason}")
            return TradeSignal(
                symbol=symbol, signal_type=SignalType.CLOSE,
                strategy_name=self.name, price=current_price,
                stop_loss=stop_loss, take_profit=take_profit,
                confidence=1.0, reason=reason,
            )

        # 利確ライン到達
        if current_price >= take_profit:
            reason = f"利確ライン到達: ${current_price:.2f} >= ${take_profit:.2f}"
            logger.info(f"[{symbol}] {reason}")
            return TradeSignal(
                symbol=symbol, signal_type=SignalType.CLOSE,
                strategy_name=self.name, price=current_price,
                stop_loss=stop_loss, take_profit=take_profit,
                confidence=1.0, reason=reason,
            )

        # 最大保有日数超過
        max_days = self.swing_config["hold_days_max"]
        if hold_days >= max_days:
            reason = f"保有日数上限到達: {hold_days}日 >= {max_days}日"
            logger.info(f"[{symbol}] {reason}")
            return TradeSignal(
                symbol=symbol, signal_type=SignalType.CLOSE,
                strategy_name=self.name, price=current_price,
                stop_loss=stop_loss, take_profit=take_profit,
                confidence=0.8, reason=reason,
            )

        # デッドクロス発生（MAクロス系エントリーの場合）
        if "ema20" in df.columns and "ema50" in df.columns:
            last = df.iloc[-1]
            prev = df.iloc[-2]
            dead_cross = prev["ema20"] >= prev["ema50"] and last["ema20"] < last["ema50"]
            if dead_cross and current_price > entry_price:
                reason = "デッドクロス発生：利益確定クローズ"
                return TradeSignal(
                    symbol=symbol, signal_type=SignalType.CLOSE,
                    strategy_name=self.name, price=current_price,
                    stop_loss=stop_loss, take_profit=take_profit,
                    confidence=0.75, reason=reason,
                )

        return None
