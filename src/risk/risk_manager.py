"""
リスク管理モジュール
テスタ氏の「負けないことを最優先」の核心部分

日次損失管理・ドローダウン管理・緊急停止機能を実装
"""
from typing import Dict, List, Optional, Tuple
from datetime import datetime, date
import pytz
from loguru import logger


class RiskManager:
    """
    全体的なリスクを管理するクラス
    個別取引とポートフォリオ全体の両方を監視する
    """

    def __init__(self, settings: dict):
        self.portfolio_config = settings["portfolio"]
        self.testa_rules = settings["testa_rules"]
        self.et_tz = pytz.timezone("America/New_York")

        # 日次・週次の損益追跡
        self.daily_pnl: float = 0.0
        self.weekly_pnl: float = 0.0
        self.start_of_day_value: Optional[float] = None
        self.start_of_week_value: Optional[float] = None
        self.peak_value: Optional[float] = None
        self.bot_paused: bool = False
        self.pause_reason: str = ""

        self.daily_loss_limit = self.portfolio_config["daily_loss_limit_pct"]
        self.weekly_loss_limit = self.portfolio_config["weekly_loss_limit_pct"]
        self.drawdown_pause_pct = self.portfolio_config["drawdown_pause_pct"]

    def initialize_day(self, portfolio_value: float):
        """取引日の開始時に呼ぶ。基準値を設定する"""
        self.start_of_day_value = portfolio_value
        self.daily_pnl = 0.0
        self.bot_paused = False
        self.pause_reason = ""

        if self.peak_value is None or portfolio_value > self.peak_value:
            self.peak_value = portfolio_value

        logger.info(f"取引日開始: ポートフォリオ${portfolio_value:.2f}")

    def initialize_week(self, portfolio_value: float):
        """週の開始時に呼ぶ"""
        self.start_of_week_value = portfolio_value
        self.weekly_pnl = 0.0
        logger.info(f"取引週開始: ポートフォリオ${portfolio_value:.2f}")

    def update_pnl(self, current_portfolio_value: float) -> Dict:
        """
        現在のPnL（損益）を更新して、リスクチェックを行う
        市場時間中に定期的に呼ぶ
        """
        # ポートフォリオ残高が0の場合はスキップ（IBKR接続直後にデータ未取得の場合がある）
        if current_portfolio_value <= 0:
            logger.warning("ポートフォリオ残高が$0。IBKRデータ取得待ち...")
            return {"status": "ok", "action": None}

        if self.start_of_day_value is None or self.start_of_day_value <= 0:
            self.initialize_day(current_portfolio_value)
            return {"status": "ok", "action": None}

        self.daily_pnl = current_portfolio_value - self.start_of_day_value
        daily_pnl_pct = self.daily_pnl / self.start_of_day_value if self.start_of_day_value > 0 else 0

        if self.start_of_week_value:
            self.weekly_pnl = current_portfolio_value - self.start_of_week_value

        # ドローダウン計算（ピークから現在まで）
        if self.peak_value and self.peak_value > 0:
            drawdown = (self.peak_value - current_portfolio_value) / self.peak_value
        else:
            drawdown = 0.0

        result = {
            "portfolio_value": current_portfolio_value,
            "daily_pnl": self.daily_pnl,
            "daily_pnl_pct": round(daily_pnl_pct * 100, 2),
            "weekly_pnl": self.weekly_pnl,
            "drawdown_pct": round(drawdown * 100, 2),
            "status": "ok",
            "action": None,
        }

        # 日次損失上限チェック（最優先）
        if daily_pnl_pct <= -self.daily_loss_limit:
            result["status"] = "danger"
            result["action"] = "stop_all"
            reason = f"日次損失上限到達: {daily_pnl_pct*100:.1f}% (上限: -{self.daily_loss_limit*100:.0f}%)"
            self._pause_bot(reason)
            result["pause_reason"] = reason
            logger.critical(f"⛔ {reason}")

        # 週次損失上限チェック（日次と同様にBot停止する：累積損失保護）
        elif self.start_of_week_value and self.weekly_pnl / self.start_of_week_value <= -self.weekly_loss_limit:
            weekly_pnl_pct = self.weekly_pnl / self.start_of_week_value
            result["status"] = "danger"
            result["action"] = "stop_all"
            reason = (
                f"週次損失上限到達: {weekly_pnl_pct*100:.1f}% "
                f"(上限: -{self.weekly_loss_limit*100:.0f}%)"
            )
            self._pause_bot(reason)
            result["pause_reason"] = reason
            logger.critical(f"⛔ {reason}")

        # ドローダウン上限チェック
        elif drawdown >= self.drawdown_pause_pct:
            result["status"] = "danger"
            result["action"] = "pause"
            reason = f"ドローダウン上限到達: {drawdown*100:.1f}% (上限: {self.drawdown_pause_pct*100:.0f}%)"
            self._pause_bot(reason)
            result["pause_reason"] = reason
            logger.critical(f"⛔ {reason}")

        return result

    def check_trade_risk(
        self,
        symbol: str,
        shares: int,
        price: float,
        stop_loss: float,
        portfolio_value: float,
        current_positions: List[Dict],
    ) -> Tuple[bool, str]:
        """
        個別取引のリスクをチェックする（エントリー前の最終確認）
        """
        if self.bot_paused:
            return False, f"Bot停止中: {self.pause_reason}"

        # 1取引のリスク金額チェック
        risk_amount = shares * (price - stop_loss)
        risk_pct = risk_amount / portfolio_value
        max_risk = self.testa_rules["stop_loss"]["max_pct"]

        if risk_pct > max_risk:
            return False, f"取引リスク超過: {risk_pct*100:.1f}% > {max_risk*100:.0f}%"

        # 同一銘柄の重複ポジションチェック
        for pos in current_positions:
            if pos.get("symbol") == symbol:
                return False, f"既存ポジションあり: {symbol}"

        # 現在の損失状態でのリスクチェック
        if self.start_of_day_value and self.daily_pnl < 0:
            daily_loss_pct = abs(self.daily_pnl) / self.start_of_day_value
            remaining_limit = self.daily_loss_limit - daily_loss_pct
            if remaining_limit < risk_pct:
                return False, f"本取引のリスクが日次損失残高を超えます ({risk_pct*100:.1f}% > 残り{remaining_limit*100:.1f}%)"

        return True, "リスクチェック通過"

    def get_risk_summary(self) -> Dict:
        """現在のリスク状態サマリーを返す"""
        return {
            "bot_paused": self.bot_paused,
            "pause_reason": self.pause_reason,
            "daily_pnl": self.daily_pnl,
            "weekly_pnl": self.weekly_pnl,
            "start_of_day_value": self.start_of_day_value,
            "peak_value": self.peak_value,
        }

    def calculate_trailing_stop(
        self,
        entry_price: float,
        current_price: float,
        original_stop: float,
        trailing_pct: Optional[float] = None,
    ) -> float:
        """
        トレーリングストップを計算する
        利益が出た場合にストップを引き上げて利益を守る
        テスタ氏の「利益は伸ばす」原則
        """
        if trailing_pct is None:
            trailing_pct = self.testa_rules["take_profit"]["trailing_stop_pct"]

        new_stop = current_price * (1 - trailing_pct)

        # ストップは上方向にしか動かさない（下げない）
        return max(original_stop, round(new_stop, 2))

    def _pause_bot(self, reason: str):
        """Botを一時停止する"""
        self.bot_paused = True
        self.pause_reason = reason

    def resume_bot(self):
        """Botを再開する（手動確認後）"""
        self.bot_paused = False
        self.pause_reason = ""
        logger.info("Bot再開")

    def is_trading_allowed(self) -> Tuple[bool, str]:
        """取引が許可されているか確認する"""
        if self.bot_paused:
            return False, self.pause_reason
        return True, "取引許可"
