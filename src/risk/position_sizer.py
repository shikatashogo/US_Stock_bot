"""
ポジションサイジングモジュール
テスタ氏の「1取引で資産を傾けない」原則の実装

Kelly基準とリスク%法を組み合わせた安全なサイジング
"""
import math
from typing import Optional, Dict, Tuple
from loguru import logger


class PositionSizer:
    """
    ポジションサイズを計算するクラス
    資金管理の厳守がテスタ哲学の根幹
    """

    def __init__(self, settings: dict):
        self.portfolio_config = settings["portfolio"]
        self.stop_config = settings["testa_rules"]["stop_loss"]
        self.max_position_pct = self.portfolio_config["max_position_pct"]
        # risk_per_trade_pct: portfolio設定を優先、なければstop_lossのdefault_pctにフォールバック
        self.risk_per_trade_pct = self.portfolio_config.get(
            "risk_per_trade_pct", self.stop_config["default_pct"]
        )

    def calculate_shares(
        self,
        portfolio_value: float,
        price: float,
        stop_loss: float,
        risk_pct: Optional[float] = None,
        volume_multiplier: float = 1.0,
    ) -> Dict:
        """
        リスク金額からポジションサイズを計算する

        計算式:
        リスク金額 = ポートフォリオ × risk_pct
        1株あたりリスク = エントリー価格 - ストップロス
        株数 = リスク金額 / 1株あたりリスク

        volume_multiplier: VIX高騰時などにポジションを縮小（0.5〜1.0）
        """
        if price <= 0 or stop_loss <= 0:
            logger.error("価格またはストップロスが無効です")
            return self._empty_result()

        risk_per_pct = risk_pct or self.risk_per_trade_pct
        max_risk_amount = portfolio_value * risk_per_pct * volume_multiplier
        risk_per_share = price - stop_loss

        if risk_per_share <= 0:
            logger.error(f"ストップロス({stop_loss:.2f})がエントリー価格({price:.2f})以上です")
            return self._empty_result()

        # リスクベースの株数
        risk_based_shares = math.floor(max_risk_amount / risk_per_share)

        # 最大ポジション制限（資産の最大X%まで）
        max_position_value = portfolio_value * self.max_position_pct
        max_by_position_limit = math.floor(max_position_value / price)

        # より小さい方を採用（安全優先）
        shares = min(risk_based_shares, max_by_position_limit)
        shares = max(shares, 0)

        if shares == 0:
            logger.warning(
                f"計算されたポジションサイズが0株です "
                f"(ポートフォリオ: ${portfolio_value:.0f}, 株価: ${price:.2f}, "
                f"ストップ: ${stop_loss:.2f})"
            )

        position_value = shares * price
        actual_risk = shares * risk_per_share
        actual_risk_pct = (actual_risk / portfolio_value) * 100 if portfolio_value > 0 else 0

        return {
            "shares": shares,
            "position_value": round(position_value, 2),
            "risk_amount": round(actual_risk, 2),
            "risk_pct": round(actual_risk_pct, 2),
            "position_pct": round((position_value / portfolio_value) * 100, 2) if portfolio_value > 0 else 0,
            "price": price,
            "stop_loss": stop_loss,
            "risk_per_share": round(risk_per_share, 2),
        }

    def can_open_position(
        self,
        portfolio_value: float,
        current_positions: int,
        shares: int,
        price: float,
        cash_balance: float,
    ) -> Tuple[bool, str]:
        """
        新しいポジションを開けるか確認する
        テスタ氏の「過剰なポジションを持たない」原則
        """
        max_positions = self.portfolio_config["max_positions"]
        if current_positions >= max_positions:
            return False, f"最大ポジション数({max_positions})に達しています"

        required_cash = shares * price
        if required_cash > cash_balance:
            return False, f"現金不足: 必要${required_cash:.0f} > 残高${cash_balance:.0f}"

        if shares == 0:
            return False, "ポジションサイズが0株（資金不足の可能性）"

        return True, "ポジション開設可能"

    def _empty_result(self) -> Dict:
        return {
            "shares": 0,
            "position_value": 0,
            "risk_amount": 0,
            "risk_pct": 0,
            "position_pct": 0,
            "price": 0,
            "stop_loss": 0,
            "risk_per_share": 0,
        }
