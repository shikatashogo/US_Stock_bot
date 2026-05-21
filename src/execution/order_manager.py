"""
注文管理モジュール
シグナルを実際の注文に変換してIBKRに送信する
リスク管理と連携して安全な取引を実行する
"""
from typing import Optional, Dict, List
from datetime import datetime
import pytz
from loguru import logger

from ..data.ibkr_client import IBKRClient
from ..data.market_data import MarketDataFetcher
from ..strategies.base_strategy import TradeSignal, SignalType
from ..risk.position_sizer import PositionSizer
from ..risk.risk_manager import RiskManager
from .trade_logger import TradeLogger


class OrderManager:
    """注文の発注・管理を担当するクラス"""

    def __init__(
        self,
        ibkr: IBKRClient,
        data: MarketDataFetcher,
        sizer: PositionSizer,
        risk_manager: RiskManager,
        logger_db: TradeLogger,
        settings: dict,
    ):
        self.ibkr = ibkr
        self.data = data
        self.sizer = sizer
        self.risk_manager = risk_manager
        self.trade_logger = logger_db
        self.settings = settings
        self.et_tz = pytz.timezone("America/New_York")
        self._active_stops: Dict[int, float] = {}  # trade_id -> stop_price

    def execute_signal(
        self,
        signal: TradeSignal,
        trade_type: str = "swing",
        volume_multiplier: float = 1.0,
    ) -> Optional[Dict]:
        """
        TradeSignalを実際の注文として執行する
        テスタ哲学: ルールに従って機械的に実行、感情を排除
        """
        # 取引許可チェック
        allowed, reason = self.risk_manager.is_trading_allowed()
        if not allowed:
            logger.warning(f"取引停止中のためスキップ: {reason}")
            return None

        # シグナル数値の妥当性検証（NaN・負・極端値の防御）
        import math
        for fld_name, fld_val in [
            ("price", signal.price),
            ("stop_loss", signal.stop_loss),
            ("take_profit", signal.take_profit),
        ]:
            if fld_val is None or math.isnan(fld_val) or fld_val <= 0:
                logger.error(f"[{signal.symbol}] 不正な{fld_name}={fld_val} のためスキップ")
                return None

        # ストップが価格より上、または利確がストップより下になっていないか
        if signal.stop_loss >= signal.price:
            logger.error(
                f"[{signal.symbol}] ストップ${signal.stop_loss:.2f} >= 価格${signal.price:.2f}"
                " という不整合シグナルのためスキップ"
            )
            return None
        if signal.take_profit <= signal.price:
            logger.error(
                f"[{signal.symbol}] 利確${signal.take_profit:.2f} <= 価格${signal.price:.2f}"
                " という不整合シグナルのためスキップ"
            )
            return None

        # PDTルール保護チェック（デイトレードのみ）
        portfolio_cfg = self.settings.get("portfolio", {})
        if trade_type == "day" and portfolio_cfg.get("pdt_protection", False):
            max_day_trades = portfolio_cfg.get("pdt_max_day_trades_per_week", 3)
            if max_day_trades == 0:
                logger.warning(f"[{signal.symbol}] PDT保護: pdt_max_day_trades_per_week=0 のためデイトレード無効")
                return None
            used = self.trade_logger.count_day_trades_this_week()
            if used >= max_day_trades:
                logger.warning(
                    f"[{signal.symbol}] PDT保護: 今週のデイトレード回数上限到達 "
                    f"({used}/{max_day_trades}回)"
                )
                return None

        portfolio_value = self.ibkr.get_portfolio_value()
        cash_balance = self.ibkr.get_cash_balance()
        current_positions = self.ibkr.get_positions()

        # ポジションサイズ計算
        size_result = self.sizer.calculate_shares(
            portfolio_value=portfolio_value,
            price=signal.price,
            stop_loss=signal.stop_loss,
            volume_multiplier=volume_multiplier,
        )

        shares = size_result["shares"]
        if shares == 0:
            logger.warning(f"[{signal.symbol}] ポジションサイズ0のためスキップ")
            return None

        # ポジション開設可否チェック
        can_open, check_msg = self.sizer.can_open_position(
            portfolio_value, len(current_positions), shares, signal.price, cash_balance
        )
        if not can_open:
            logger.warning(f"[{signal.symbol}] ポジション開設不可: {check_msg}")
            return None

        # 最終リスクチェック
        risk_ok, risk_msg = self.risk_manager.check_trade_risk(
            signal.symbol, shares, signal.price, signal.stop_loss,
            portfolio_value, current_positions
        )
        if not risk_ok:
            logger.warning(f"[{signal.symbol}] リスクチェック不通過: {risk_msg}")
            return None

        # 注文実行
        logger.info(
            f"[{signal.symbol}] 注文実行: {shares}株 @ ${signal.price:.2f} "
            f"| ストップ: ${signal.stop_loss:.2f} | 目標: ${signal.take_profit:.2f}"
        )

        if signal.signal_type == SignalType.BUY:
            order_result = self.ibkr.place_market_order(signal.symbol, "BUY", shares)
        else:
            order_result = self.ibkr.place_market_order(signal.symbol, "SELL", shares)

        if not order_result:
            logger.error(f"[{signal.symbol}] 注文発注失敗")
            return None

        # ストップロス注文を同時に発注（損切りを自動化）
        # スイング: GTC（翌日以降も有効）、デイトレ: DAY（当日限り）
        # 失敗時はリトライ → それでも失敗ならポジションを緊急成行クローズ（裸放置を絶対回避）
        stop_action = "SELL" if signal.signal_type == SignalType.BUY else "BUY"
        stop_order = None
        for attempt in range(3):
            stop_order = self.ibkr.place_stop_order(
                signal.symbol, stop_action, shares, signal.stop_loss, trade_type=trade_type
            )
            if stop_order is not None:
                break
            logger.warning(f"[{signal.symbol}] ストップ注文リトライ {attempt+1}/3")

        if stop_order is None:
            logger.critical(
                f"[{signal.symbol}] ストップ注文が3回失敗。ポジションを緊急成行クローズします（裸ポジション回避）"
            )
            self.ibkr.close_position(signal.symbol)
            return None

        # 実約定価格があればそれを使用（成行スリッページ対応）
        # 約定価格がシグナル価格と大きく乖離した場合はストップ/利確も再計算する必要があるが
        # 現状は実約定値で記録するのみ（テスタ哲学：記録の正確性を優先）
        actual_entry = order_result.get("avg_fill_price") or signal.price
        if actual_entry <= 0:
            actual_entry = signal.price

        # 取引ログに記録（実約定価格で）
        trade_id = self.trade_logger.log_entry(
            symbol=signal.symbol,
            strategy=signal.strategy_name,
            trade_type=trade_type,
            shares=shares,
            entry_price=actual_entry,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            signal_reason=signal.reason,
            testa_score=signal.metadata.get("testa_score", 0),
        )

        self._active_stops[trade_id] = signal.stop_loss

        return {
            "trade_id": trade_id,
            "symbol": signal.symbol,
            "shares": shares,
            "entry_price": actual_entry,
            "signal_price": signal.price,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "strategy": signal.strategy_name,
            "order_result": order_result,
        }

    def monitor_positions(self) -> List[Dict]:
        """
        オープンポジションを監視してエグジット条件を確認する
        トレーリングストップも更新する（IBKRのストップ注文を実際に置き換える）
        """
        open_trades = self.trade_logger.get_open_trades()
        actions_taken = []

        for trade in open_trades:
            symbol = trade["symbol"]
            current_price = self.data.get_current_price(symbol)

            if current_price is None:
                continue

            trade_id = trade["id"]
            entry_price = trade["entry_price"]
            stop_loss = trade["stop_loss"]
            take_profit = trade["take_profit"]

            # トレーリングストップの更新（IBKR側の実際のストップ注文も置換する）
            if current_price > entry_price:
                new_stop = self.risk_manager.calculate_trailing_stop(
                    entry_price, current_price, stop_loss
                )
                # 0.5%以上の改善があるときのみ置換（過剰なAPIコール防止・スプレッド吸収）
                if new_stop > stop_loss * 1.005:
                    trade_type = trade.get("trade_type", "swing")
                    shares = trade["shares"]
                    if self._update_ibkr_stop_order(symbol, shares, new_stop, trade_type):
                        # IBKR置換成功 → DBも更新（再起動・他ループでも反映されるように）
                        self.trade_logger.update_stop_loss(trade_id, new_stop)
                        stop_loss = new_stop
                        self._active_stops[trade_id] = new_stop
                        logger.info(
                            f"[{symbol}] トレーリングストップ更新: ${trade['stop_loss']:.2f} → ${new_stop:.2f} "
                            f"(現在価格 ${current_price:.2f})"
                        )
                    else:
                        logger.warning(f"[{symbol}] IBKRストップ置換失敗。DBは更新しません")

            # 損切りチェック（最優先）
            if current_price <= stop_loss:
                result = self._close_position(
                    trade_id, symbol, current_price,
                    f"損切り実行: ${current_price:.2f} <= ${stop_loss:.2f}"
                )
                if result:
                    actions_taken.append(result)

            # 利確チェック
            elif current_price >= take_profit:
                result = self._close_position(
                    trade_id, symbol, current_price,
                    f"利確実行: ${current_price:.2f} >= ${take_profit:.2f}"
                )
                if result:
                    actions_taken.append(result)

        return actions_taken

    def close_all_day_trades(self):
        """
        デイトレードポジションを全てクローズする
        市場クローズ前に呼ぶ
        """
        open_trades = self.trade_logger.get_open_trades()
        closed = []

        for trade in open_trades:
            if trade.get("trade_type") != "day":
                continue

            symbol = trade["symbol"]
            current_price = self.data.get_current_price(symbol)
            if current_price is None:
                current_price = trade["entry_price"]  # フォールバック

            result = self._close_position(
                trade["id"], symbol, current_price, "デイトレード EOD強制クローズ"
            )
            if result:
                closed.append(result)

        logger.info(f"デイトレード EODクローズ完了: {len(closed)}件")
        return closed

    def _update_ibkr_stop_order(
        self,
        symbol: str,
        shares: int,
        new_stop_price: float,
        trade_type: str = "swing",
    ) -> bool:
        """
        IBKRに置かれている既存のストップ注文をキャンセルして新しい価格で再発注する
        トレーリングストップを実際にブローカー側に反映するために使用

        戻り値: True=置換成功 / False=置換失敗（DBは更新しないこと）
        """
        try:
            # 該当銘柄のオープンSTP注文を検索
            existing_stops = [
                o for o in self.ibkr.get_open_orders()
                if o["symbol"] == symbol and o["order_type"] == "STP"
            ]

            # 既存ストップをキャンセル
            for stop_order in existing_stops:
                self.ibkr.cancel_order(stop_order["order_id"])

            # 新しい価格でストップを再発注
            new_stop_result = self.ibkr.place_stop_order(
                symbol, "SELL", shares, new_stop_price, trade_type=trade_type
            )

            if new_stop_result is None:
                logger.error(
                    f"[{symbol}] トレーリングストップ再発注失敗。"
                    f"既存ストップキャンセル済みのためポジションが裸状態の可能性あり"
                )
                # 緊急対応: 緊急成行クローズで保護（リカバリ）
                logger.warning(f"[{symbol}] 安全のためポジションを緊急クローズします")
                self.ibkr.close_position(symbol)
                return False

            return True
        except Exception as e:
            logger.error(f"[{symbol}] ストップ注文置換エラー: {e}")
            return False

    def _close_position(
        self,
        trade_id: int,
        symbol: str,
        current_price: float,
        reason: str,
    ) -> Optional[Dict]:
        """ポジションをクローズする内部メソッド"""
        order_result = self.ibkr.close_position(symbol)
        if not order_result:
            # IBKRにポジションが存在しない場合はゴーストトレードとしてDBをキャンセルに更新
            ibkr_symbols = {p["symbol"] for p in self.ibkr.get_positions()}
            if symbol not in ibkr_symbols:
                logger.warning(f"[{symbol}] IBKRにポジションなし。DBトレード(ID:{trade_id})をキャンセルに更新")
                self.trade_logger.mark_as_cancelled(trade_id, "IBKRにポジション未存在のためキャンセル")
            else:
                logger.error(f"[{symbol}] クローズ注文失敗")
            return None

        # ストップ注文をキャンセル
        for order in self.ibkr.get_open_orders():
            if order["symbol"] == symbol and order["order_type"] == "STP":
                self.ibkr.cancel_order(order["order_id"])

        exit_result = self.trade_logger.log_exit(
            trade_id=trade_id,
            exit_price=current_price,
            exit_reason=reason,
        )

        if trade_id in self._active_stops:
            del self._active_stops[trade_id]

        return exit_result
