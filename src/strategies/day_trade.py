"""
デイトレード戦略
当日中に決済する短期トレード（PDTルール考慮）

注意: 米国ではPDTルール($25,000未満はデイトレ3回/週まで)があります
資金が少ない段階では取引機会を厳選し、高確率なセットアップのみ実行

テスタ哲学ベースの3つのデイトレ戦略:
1. VWAPバウンス: VWAPに戻ってきた際の反発を狙う
2. 寄り付きブレイクアウト(ORB): 最初15分の値幅をブレイクした方向で入る
3. モメンタム: 強いモメンタムのある銘柄に乗る
"""
from typing import Optional, Dict, Tuple
import pandas as pd
from datetime import datetime, time
import pytz
from loguru import logger

from .base_strategy import BaseStrategy, TradeSignal, SignalType
from .testa_rules import TestaRulesEngine


class DayTradeStrategy(BaseStrategy):
    """デイトレード戦略"""

    def __init__(self, settings: dict):
        super().__init__(settings)
        self.day_config = settings["day_trade"]
        self.testa_engine = TestaRulesEngine(settings)
        self.vwap_config = self.day_config["vwap_bounce"]
        self.orb_config = self.day_config["orb"]
        self.momentum_config = self.day_config["momentum"]
        self.et_tz = pytz.timezone("America/New_York")
        self._orb_ranges: Dict[str, Dict] = {}  # 銘柄別ORBレンジ

    @property
    def name(self) -> str:
        return "DayTrade"

    def is_tradeable_time(self) -> bool:
        """現在の時刻が取引可能時間帯か確認する"""
        now = datetime.now(self.et_tz)
        current_time = now.time()

        start = time(*map(int, self.day_config["trading_hours"]["start"].split(":")))
        end = time(*map(int, self.day_config["trading_hours"]["end"].split(":")))

        if not (start <= current_time <= end):
            return False

        # ランチタイム回避（11:30-13:00は出来高が薄い）
        if self.day_config["trading_hours"]["avoid_lunch"]:
            lunch_start = time(11, 30)
            lunch_end = time(13, 0)
            if lunch_start <= current_time <= lunch_end:
                return False

        return True

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        market_status: Dict
    ) -> Optional[TradeSignal]:
        """デイトレシグナルを生成する（5分足データを使用）"""
        # day_trade.enabled=false の場合はシグナル生成しない（ORB/VWAP等の内部ログも出さない）
        if not self.day_config.get("enabled", True):
            return None

        if not self.check_market_condition(market_status):
            return None

        if not self.is_tradeable_time():
            return None

        if df is None or len(df) < 30:
            return None

        signals = []

        # 戦略1: VWAPバウンス
        if self.vwap_config["enabled"]:
            signal = self._vwap_bounce_signal(symbol, df, market_status)
            if signal:
                signals.append(signal)

        # 戦略2: 寄り付きブレイクアウト
        if self.orb_config["enabled"]:
            signal = self._orb_signal(symbol, df, market_status)
            if signal:
                signals.append(signal)

        # 戦略3: モメンタム
        if self.momentum_config["enabled"]:
            signal = self._momentum_signal(symbol, df, market_status)
            if signal:
                signals.append(signal)

        if not signals:
            return None

        best_signal = max(signals, key=lambda s: s.confidence)

        if not self.validate_signal(best_signal):
            return None

        return best_signal

    def _vwap_bounce_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        market_status: Dict
    ) -> Optional[TradeSignal]:
        """
        VWAPバウンス戦略
        株価がVWAPに近づいたときの反発を狙う
        テスタ氏の「VWAP付近で待つ」原則
        """
        if "vwap" not in df.columns:
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]

        price = last["close"]
        vwap = last["vwap"]
        deviation_threshold = self.vwap_config["deviation_entry"]

        # 株価がVWAPより下からVWAPに近づいてきた（買いシグナル）
        price_below_vwap_prev = prev["close"] < prev["vwap"] * (1 - deviation_threshold)
        approaching_vwap = last["close"] >= last["vwap"] * (1 - deviation_threshold * 0.5)

        if not (price_below_vwap_prev and approaching_vwap):
            return None

        # 出来高確認
        volume_spike = last.get("volume_ratio", 1.0) >= self.vwap_config["min_volume_spike"]
        if not volume_spike:
            return None

        # テスタルール（市場フィルターのみ）
        vix = market_status.get("vix", 20)
        if vix and vix > self.testa_rules["market_filter"]["vix_max"]:
            return None

        atr = last.get("atr", price * 0.01)
        stop_loss = round(vwap * (1 - self.vwap_config["deviation_entry"] * 2), 2)
        stop_loss = max(stop_loss, self.calculate_stop_loss(price, atr, "long"))
        take_profit = self.calculate_take_profit(price, stop_loss, "long")

        confidence = 0.65
        if last.get("volume_ratio", 1.0) > 2.5:
            confidence += 0.1
        if market_status.get("spy_trend") == "up":
            confidence += 0.05

        reason = (
            f"VWAPバウンス: 株価${price:.2f} ≈ VWAP${vwap:.2f} "
            f"| 出来高{last.get('volume_ratio', 1.0):.1f}x"
        )

        logger.info(f"[{symbol}] デイトレ VWAPバウンスシグナル: ${price:.2f}")

        return TradeSignal(
            symbol=symbol,
            signal_type=SignalType.BUY,
            strategy_name="DayVWAP",
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=min(confidence, 0.9),
            reason=reason,
            metadata={"vwap": vwap, "strategy_sub": "vwap_bounce"},
        )

    def _orb_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        market_status: Dict
    ) -> Optional[TradeSignal]:
        """
        Opening Range Breakout（寄り付きブレイクアウト）戦略
        マーケットオープン後15分の高値/安値をブレイクした方向へエントリー
        """
        range_minutes = self.orb_config["range_minutes"]
        buffer = self.orb_config["breakout_buffer_pct"]

        now = datetime.now(self.et_tz)
        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        orb_end = now.replace(hour=9, minute=30 + range_minutes, second=0, microsecond=0)

        # ORBレンジ確定時間より前は計算しない
        if now <= orb_end:
            return None

        # ORBレンジの計算（当日のオープンからrange_minutes分間のデータ）
        today_data = df[df.index.date == now.date()]
        orb_data = today_data[today_data.index.time <= orb_end.time()]

        if len(orb_data) < 3:
            return None

        orb_high = orb_data["high"].max()
        orb_low = orb_data["low"].min()
        self._orb_ranges[symbol] = {"high": orb_high, "low": orb_low}

        last = df.iloc[-1]
        price = last["close"]

        # 上方ブレイクアウト
        if price > orb_high * (1 + buffer):
            atr = last.get("atr", price * 0.01)
            stop_loss = max(orb_high * 0.99, self.calculate_stop_loss(price, atr, "long"))
            take_profit = self.calculate_take_profit(price, stop_loss, "long")

            confidence = 0.68
            if last.get("volume_ratio", 1.0) > 1.5:
                confidence += 0.08
            if market_status.get("spy_trend") == "up":
                confidence += 0.07

            reason = (
                f"ORBブレイクアウト（上）: ${price:.2f} > ORB高値${orb_high:.2f} "
                f"| ORBレンジ: ${orb_low:.2f}〜${orb_high:.2f}"
            )

            logger.info(f"[{symbol}] デイトレ ORBブレイクアウト(上): ${price:.2f}")

            return TradeSignal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                strategy_name="DayORB",
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=min(confidence, 0.9),
                reason=reason,
                metadata={"orb_high": orb_high, "orb_low": orb_low, "strategy_sub": "orb"},
            )

        return None

    def _momentum_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        market_status: Dict
    ) -> Optional[TradeSignal]:
        """
        モメンタム戦略
        強いモメンタムのある銘柄に乗る（テスタ氏の「強い銘柄を追え」）
        """
        lookback = self.momentum_config["lookback_minutes"]
        min_momentum = self.momentum_config["min_momentum_pct"]

        if len(df) < lookback // 5 + 1:
            return None

        last = df.iloc[-1]
        lookback_bars = lookback // 5  # 5分足でのバー数
        past = df.iloc[-(lookback_bars + 1)]

        momentum_pct = (last["close"] - past["close"]) / past["close"]

        if momentum_pct < min_momentum:
            return None

        # 出来高も増加していること
        if last.get("volume_ratio", 1.0) < 1.5:
            return None

        # VIXチェック
        vix = market_status.get("vix", 20)
        if vix and vix > self.testa_rules["market_filter"]["vix_max"]:
            return None

        price = last["close"]
        atr = last.get("atr", price * 0.01)
        stop_loss = self.calculate_stop_loss(price, atr, "long")
        take_profit = self.calculate_take_profit(price, stop_loss, "long")

        confidence = 0.60 + min(momentum_pct * 10, 0.2)
        if last.get("volume_ratio", 1.0) > 2.0:
            confidence += 0.05

        reason = (
            f"モメンタム買いシグナル: {lookback}分で{momentum_pct*100:.1f}%上昇 "
            f"| 出来高{last.get('volume_ratio', 1.0):.1f}x"
        )

        logger.info(f"[{symbol}] デイトレ モメンタムシグナル: ${price:.2f} ({momentum_pct*100:.1f}%)")

        return TradeSignal(
            symbol=symbol,
            signal_type=SignalType.BUY,
            strategy_name="DayMomentum",
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=min(confidence, 0.88),
            reason=reason,
            metadata={"momentum_pct": round(momentum_pct * 100, 2), "strategy_sub": "momentum"},
        )

    def should_close_eod(self, symbol: str) -> bool:
        """
        市場クローズ前に強制決済すべきか確認する
        デイトレ: 15:45以降は必ず決済
        """
        now = datetime.now(self.et_tz)
        eod_time = time(*map(int, self.day_config["trading_hours"]["end"].split(":")))
        return now.time() >= eod_time
