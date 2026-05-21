"""
戦略基底クラス
全戦略共通のインターフェースと基本機能を定義する
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, List
from datetime import datetime
from enum import Enum
import pytz


class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    CLOSE = "CLOSE"  # ポジションクローズ


@dataclass
class TradeSignal:
    """取引シグナルを表すデータクラス"""
    symbol: str
    signal_type: SignalType
    strategy_name: str
    price: float
    stop_loss: float
    take_profit: float
    confidence: float  # 0.0 〜 1.0（シグナルの確信度）
    reason: str        # シグナル発生理由
    timestamp: datetime = field(default_factory=lambda: datetime.now(pytz.timezone("America/New_York")))
    metadata: Dict = field(default_factory=dict)

    @property
    def risk_reward_ratio(self) -> float:
        """リスクリワード比を計算する"""
        if self.signal_type == SignalType.BUY:
            risk = self.price - self.stop_loss
            reward = self.take_profit - self.price
        elif self.signal_type == SignalType.SELL:
            risk = self.stop_loss - self.price
            reward = self.price - self.take_profit
        else:
            return 0.0

        if risk <= 0:
            return 0.0
        return reward / risk

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "signal_type": self.signal_type.value,
            "strategy_name": self.strategy_name,
            "price": self.price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "confidence": self.confidence,
            "reason": self.reason,
            "risk_reward_ratio": round(self.risk_reward_ratio, 2),
            "timestamp": self.timestamp.isoformat(),
        }


class BaseStrategy(ABC):
    """全戦略の基底クラス"""

    def __init__(self, settings: dict):
        self.settings = settings
        self.testa_rules = settings["testa_rules"]
        self.min_rr_ratio = self.testa_rules["take_profit"]["min_rr_ratio"]
        self.default_stop_pct = self.testa_rules["stop_loss"]["default_pct"]
        self.et_tz = pytz.timezone("America/New_York")

    @property
    @abstractmethod
    def name(self) -> str:
        """戦略名"""
        pass

    @abstractmethod
    def generate_signal(self, symbol: str, df, market_status: Dict) -> Optional[TradeSignal]:
        """
        シグナルを生成する（各戦略で実装）
        df: calculate_indicators済みのDataFrame
        market_status: get_market_status()の結果
        """
        pass

    def validate_signal(self, signal: TradeSignal) -> bool:
        """
        テスタルールに基づいてシグナルを検証する
        「負けないことを最優先」の実装
        """
        # リスクリワード比のチェック
        # ※ calculate_take_profit で round() を使うため浮動小数点誤差が生じ
        #    RR=1.9982 が画面上 "2.00" と表示されながら < 2.0 で弾かれる問題がある。
        #    小数点2桁で丸めてから比較することで、表示値と判定を一致させる。
        if round(signal.risk_reward_ratio, 2) < self.min_rr_ratio:
            return False

        # ストップロス幅のチェック
        if signal.signal_type == SignalType.BUY:
            stop_pct = (signal.price - signal.stop_loss) / signal.price
        else:
            stop_pct = (signal.stop_loss - signal.price) / signal.price

        if round(stop_pct, 4) > self.testa_rules["stop_loss"]["max_pct"]:
            return False

        # 確信度チェック
        if signal.confidence < 0.6:
            return False

        return True

    def calculate_stop_loss(self, price: float, atr: float, direction: str = "long") -> float:
        """
        ATRベースのストップロス価格を計算する
        テスタ氏の「損切りは早く・確実に」原則
        """
        multiplier = self.testa_rules["stop_loss"]["atr_multiplier"]
        if direction == "long":
            return round(price - (atr * multiplier), 2)
        else:
            return round(price + (atr * multiplier), 2)

    def calculate_take_profit(self, price: float, stop_loss: float, direction: str = "long") -> float:
        """
        リスクリワード比に基づく利確価格を計算する
        """
        if direction == "long":
            risk = price - stop_loss
            return round(price + (risk * self.min_rr_ratio), 2)
        else:
            risk = stop_loss - price
            return round(price - (risk * self.min_rr_ratio), 2)

    def check_market_condition(self, market_status: Dict) -> bool:
        """
        市場環境が取引に適しているか確認する
        VIXが高すぎる場合は取引停止（テスタ哲学の「市場全体を見る」）
        """
        condition = market_status.get("market_condition", "unknown")
        vix = market_status.get("vix", 20)

        vix_max = self.testa_rules["market_filter"]["vix_max"]
        if vix and vix > vix_max:
            return False

        if condition == "extreme_fear":
            return False

        return True

    def get_volume_multiplier(self, market_status: Dict) -> float:
        """VIXに応じてポジションサイズを調整する係数を返す"""
        vix = market_status.get("vix", 20)
        if vix is None:
            return 1.0
        vix_caution = self.testa_rules["market_filter"]["vix_caution"]
        if vix > vix_caution:
            return 0.5  # 取引量半減
        return 1.0
