"""
IBKR（Interactive Brokers）接続クライアント
ib_insync ライブラリを使用してTWS/IB Gatewayに接続します
"""
import os
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import pytz

from ib_insync import IB, Stock, MarketOrder, LimitOrder, StopOrder
from ib_insync import util as ib_util
from loguru import logger


class IBKRClient:
    """IBKR APIとの接続を管理するクライアント"""

    def __init__(self, host: str, port: int, client_id: int, paper_trading: bool = True):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.paper_trading = paper_trading
        self.ib = IB()
        self._connected = False
        self.et_tz = pytz.timezone("America/New_York")
        # ※ accountSummary() はTWSへの新規サブスクリプションを毎回作成するため
        #    呼びすぎると Error 322 が発生する。代わりに accountValues() を使う。
        #    accountValues() はTWSが自動送信するデータをib_insync内部で保持しており
        #    追加リクエスト不要で NetLiquidation 等の全フィールドが取得できる。
        # JPY/USDレートキャッシュ（1時間ごとにyfinanceから更新）
        self._jpy_usd_rate: Optional[float] = None
        self._jpy_usd_rate_ts: Optional[datetime] = None
        self._JPY_RATE_CACHE_TTL = 3600  # 秒

    def _get_jpy_usd_rate(self) -> float:
        """
        JPY/USDレートをyfinanceから取得する（1時間キャッシュ）
        取得失敗時は環境変数 JPY_USD_RATE → デフォルト0.0066 にフォールバック
        """
        fallback = float(os.getenv("JPY_USD_RATE", "0.0066"))
        now = datetime.now()

        # キャッシュが有効な場合はそのまま返す
        if (
            self._jpy_usd_rate is not None
            and self._jpy_usd_rate_ts is not None
            and (now - self._jpy_usd_rate_ts).total_seconds() < self._JPY_RATE_CACHE_TTL
        ):
            return self._jpy_usd_rate

        try:
            import yfinance as yf
            ticker = yf.Ticker("USDJPY=X")
            hist = ticker.history(period="1d", auto_adjust=True)
            if not hist.empty:
                usd_jpy = float(hist["Close"].iloc[-1])  # 例: 155.0 (1USD = 155JPY)
                rate = 1.0 / usd_jpy                     # JPY → USD変換レート
                self._jpy_usd_rate = rate
                self._jpy_usd_rate_ts = now
                logger.debug(f"JPY/USDレート更新: 1JPY = ${rate:.6f} (USD/JPY={usd_jpy:.2f})")
                return rate
        except Exception as e:
            logger.debug(f"JPY/USDレート取得失敗、フォールバック使用: {e}")

        # フォールバック（前回キャッシュがあればそちらを優先）
        if self._jpy_usd_rate is not None:
            logger.debug(f"JPY/USDレート: 前回キャッシュ使用 ({self._jpy_usd_rate:.6f})")
            return self._jpy_usd_rate
        return fallback

    def connect(self) -> bool:
        """IBKRに接続する"""
        try:
            self.ib.connect(self.host, self.port, clientId=self.client_id)
            self._connected = True
            mode = "ペーパートレード" if self.paper_trading else "本番取引"
            logger.info(f"IBKR接続成功 [{mode}] - {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"IBKR接続失敗: {e}")
            logger.info("TWSまたはIB Gatewayが起動しているか確認してください")
            self._connected = False
            return False

    def disconnect(self):
        """IBKRから切断する"""
        if self._connected:
            self.ib.disconnect()
            self._connected = False
            logger.info("IBKR切断完了")

    @property
    def is_connected(self) -> bool:
        return self._connected and self.ib.isConnected()

    def _get_account_values(self) -> List:
        """
        口座残高情報を取得する（Error 322対策: accountSummary を使わない）

        ib_insync の accountValues() はTWSが自動送信するアカウント更新イベントを
        内部キャッシュに蓄積したもの。追加サブスクリプション不要で安全に何度でも呼べる。
        accountSummary() とは異なり reqAccountSummary() を発行しないため Error 322 が発生しない。
        """
        try:
            items = self.ib.accountValues()
            if items:
                return items
        except Exception as e:
            logger.debug(f"accountValues取得エラー: {e}")
        return []

    def get_account_summary(self) -> Dict:
        """口座サマリーを取得する"""
        if not self.is_connected:
            logger.error("IBKR未接続")
            return {}

        summary = {}
        try:
            for item in self._get_account_values():
                if item.currency in ("USD", "BASE", "JPY", ""):
                    summary[item.tag] = item.value
        except Exception as e:
            logger.debug(f"accountValues取得エラー: {e}")

        if summary:
            logger.debug(f"口座データ取得: {list(summary.keys())[:5]}")
        return summary

    def get_portfolio_value(self) -> float:
        """現在のポートフォリオ総額を取得してUSDで返す"""
        if not self.is_connected:
            return 0.0

        # NetLiquidation を優先（現金+ポジション時価の合計。ポジションを持っても変動しない）
        # AvailableFunds は現金のみのため、買い注文後に急減して誤った損失判定を引き起こすため除外
        jpy_usd_rate = self._get_jpy_usd_rate()
        priority_tags = ["NetLiquidation", "EquityWithLoanValue"]

        try:
            items = self._get_account_values()
            for tag in priority_tags:
                for item in items:
                    if item.tag != tag:
                        continue
                    val = float(item.value or 0)
                    if val <= 0:
                        continue
                    if item.currency == "JPY":
                        usd_val = val * jpy_usd_rate
                        logger.debug(f"残高取得({tag}): ¥{val:,.0f} → ${usd_val:,.2f}")
                        return round(usd_val, 2)
                    elif item.currency in ("USD", "BASE", ""):
                        logger.debug(f"残高取得({tag}): ${val:,.2f}")
                        return round(val, 2)
        except Exception as e:
            logger.error(f"ポートフォリオ残高取得エラー: {e}")

        return 0.0

    def get_cash_balance(self) -> float:
        """利用可能現金残高をUSDで返す"""
        jpy_usd_rate = self._get_jpy_usd_rate()

        try:
            for item in self._get_account_values():
                if item.tag in ("AvailableFunds", "TotalCashValue", "CashBalance"):
                    val = float(item.value or 0)
                    if val <= 0:
                        continue
                    if item.currency == "JPY":
                        return round(val * jpy_usd_rate, 2)
                    elif item.currency in ("USD", "BASE", ""):
                        return round(val, 2)
        except Exception as e:
            logger.error(f"現金残高取得エラー: {e}")

        return 0.0

    def get_positions(self) -> List[Dict]:
        """現在のポジション一覧を取得する"""
        if not self.is_connected:
            return []

        positions = []
        for pos in self.ib.positions():
            positions.append({
                "symbol": pos.contract.symbol,
                "quantity": pos.position,
                "avg_cost": pos.avgCost,
                "market_value": pos.marketValue if hasattr(pos, "marketValue") else 0,
            })
        return positions

    def get_open_orders(self) -> List[Dict]:
        """未約定注文の一覧を取得する"""
        if not self.is_connected:
            return []

        orders = []
        for trade in self.ib.openTrades():
            orders.append({
                "order_id": trade.order.orderId,
                "symbol": trade.contract.symbol,
                "action": trade.order.action,
                "quantity": trade.order.totalQuantity,
                "order_type": trade.order.orderType,
                "limit_price": getattr(trade.order, "lmtPrice", None),
                "status": trade.orderStatus.status,
            })
        return orders

    def place_market_order(self, symbol: str, action: str, quantity: int) -> Optional[Dict]:
        """
        成行注文を発注する
        action: 'BUY' または 'SELL'

        実約定価格とステータスを確認してから返す（fill待ち最大10秒）
        Cancelled/Inactive は失敗扱い（None返却）
        """
        if not self.is_connected:
            logger.error("IBKR未接続のため注文不可")
            return None

        if self.paper_trading:
            logger.info(f"[ペーパー] {action} {quantity}株 {symbol} @ 成行")

        try:
            contract = Stock(symbol, "SMART", "USD")
            order = MarketOrder(action, quantity)
            order.tif = "DAY"
            trade = self.ib.placeOrder(contract, order)

            # 約定確認（最大10秒、0.5秒ごとに状態確認）
            terminal_states = {"Filled", "Cancelled", "Inactive", "ApiCancelled"}
            elapsed = 0.0
            while elapsed < 10.0:
                self.ib.sleep(0.5)
                elapsed += 0.5
                status = trade.orderStatus.status
                if status in terminal_states:
                    break

            status = trade.orderStatus.status
            avg_fill_price = trade.orderStatus.avgFillPrice or 0.0
            filled_qty = trade.orderStatus.filled or 0

            # 拒否系ステータスは失敗
            if status in ("Cancelled", "Inactive", "ApiCancelled"):
                logger.error(f"[{symbol}] 成行注文が拒否されました: status={status}")
                return None

            result = {
                "order_id": trade.order.orderId,
                "symbol": symbol,
                "action": action,
                "quantity": quantity,
                "filled_quantity": filled_qty,
                "avg_fill_price": round(avg_fill_price, 4),
                "order_type": "MKT",
                "status": status,
                "timestamp": datetime.now(self.et_tz).isoformat(),
            }

            if status == "Filled":
                logger.info(
                    f"成行注文約定: {symbol} {action} {filled_qty}株 @ ${avg_fill_price:.2f} "
                    f"(ID: {trade.order.orderId})"
                )
            else:
                logger.warning(
                    f"成行注文発注済（未約定）: {symbol} {action} {quantity}株 status={status} "
                    f"(ID: {trade.order.orderId}) - 後続処理は継続します"
                )
            return result
        except Exception as e:
            logger.error(f"注文発注エラー ({symbol}): {e}")
            return None

    def place_limit_order(self, symbol: str, action: str, quantity: int,
                          limit_price: float) -> Optional[Dict]:
        """指値注文を発注する"""
        if not self.is_connected:
            logger.error("IBKR未接続のため注文不可")
            return None

        try:
            contract = Stock(symbol, "SMART", "USD")
            order = LimitOrder(action, quantity, limit_price)
            order.tif = "DAY"
            trade = self.ib.placeOrder(contract, order)
            self.ib.sleep(1)

            result = {
                "order_id": trade.order.orderId,
                "symbol": symbol,
                "action": action,
                "quantity": quantity,
                "order_type": "LMT",
                "limit_price": limit_price,
                "status": trade.orderStatus.status,
                "timestamp": datetime.now(self.et_tz).isoformat(),
            }
            logger.info(f"指値注文発注: {symbol} {action} {quantity}株 @ ${limit_price:.2f}")
            return result
        except Exception as e:
            logger.error(f"指値注文エラー ({symbol}): {e}")
            return None

    def place_stop_order(self, symbol: str, action: str, quantity: int,
                         stop_price: float, trade_type: str = "swing") -> Optional[Dict]:
        """
        ストップ注文（損切り注文）を発注する
        trade_type: "swing" → GTC（翌日以降も有効）/ "day" → DAY（当日限り）
        スイングトレードのストップ注文は翌日も有効である必要があるため GTC を使う
        """
        if not self.is_connected:
            return None

        try:
            contract = Stock(symbol, "SMART", "USD")
            order = StopOrder(action, quantity, stop_price)
            # スイング: GTC（翌営業日以降も有効）、デイトレ: DAY（当日限り・Error 10349対策）
            order.tif = "GTC" if trade_type == "swing" else "DAY"
            trade = self.ib.placeOrder(contract, order)
            self.ib.sleep(1)

            tif_label = order.tif
            result = {
                "order_id": trade.order.orderId,
                "symbol": symbol,
                "action": action,
                "quantity": quantity,
                "order_type": "STP",
                "stop_price": stop_price,
                "tif": tif_label,
                "status": trade.orderStatus.status,
                "timestamp": datetime.now(self.et_tz).isoformat(),
            }
            logger.info(f"ストップ注文発注: {symbol} {action} {quantity}株 @ ${stop_price:.2f} (TIF={tif_label})")
            return result
        except Exception as e:
            logger.error(f"ストップ注文エラー ({symbol}): {e}")
            return None

    def cancel_order(self, order_id: int) -> bool:
        """注文をキャンセルする"""
        if not self.is_connected:
            return False

        try:
            for trade in self.ib.openTrades():
                if trade.order.orderId == order_id:
                    self.ib.cancelOrder(trade.order)
                    logger.info(f"注文キャンセル: ID {order_id}")
                    return True
            logger.warning(f"注文ID {order_id} が見つかりません")
            return False
        except Exception as e:
            logger.error(f"注文キャンセルエラー: {e}")
            return False

    def cancel_all_orders(self, symbol: str = None):
        """全注文またはシンボル指定で注文をキャンセルする"""
        if not self.is_connected:
            return

        cancelled = 0
        for trade in self.ib.openTrades():
            if symbol is None or trade.contract.symbol == symbol:
                self.ib.cancelOrder(trade.order)
                cancelled += 1
        logger.info(f"{cancelled}件の注文をキャンセルしました")

    def close_position(self, symbol: str) -> Optional[Dict]:
        """指定シンボルのポジションを成行でクローズする"""
        positions = self.get_positions()
        for pos in positions:
            if pos["symbol"] == symbol:
                quantity = abs(int(pos["quantity"]))
                action = "SELL" if pos["quantity"] > 0 else "BUY"
                logger.info(f"ポジションクローズ: {symbol} {action} {quantity}株")
                return self.place_market_order(symbol, action, quantity)
        logger.warning(f"{symbol} のポジションが見つかりません")
        return None

    def close_all_positions(self):
        """全ポジションをクローズする（緊急時使用）"""
        positions = self.get_positions()
        logger.warning(f"全ポジションクローズ開始: {len(positions)}件")
        for pos in positions:
            self.close_position(pos["symbol"])
