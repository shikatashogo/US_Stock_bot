"""
メインスケジューラー・オーケストレーター
全コンポーネントを統合して自動取引を制御する

タイムライン（米国東部時間 → 日本時間 +14時間）:
  9:25 ET (23:25 JST)  → 市場オープン前準備
  9:30 ET (23:30 JST)  → 市場オープン・取引開始
  9:35〜15:45 ET        → 取引継続・ポジション監視（5分毎）
  15:45 ET (05:45 JST) → デイトレEODクローズ
  16:00 ET (06:00 JST) → 市場クローズ・日次レポート送信
"""
import time
import os
import yaml
from pathlib import Path
from typing import Optional
from datetime import datetime
import pytz
from loguru import logger

from ..data.ibkr_client import IBKRClient
from ..data.market_data import MarketDataFetcher
from ..data.universe import UniverseManager
from ..strategies.swing_trade import SwingTradeStrategy
from ..strategies.day_trade import DayTradeStrategy
from ..risk.position_sizer import PositionSizer
from ..risk.risk_manager import RiskManager
from ..execution.order_manager import OrderManager
from ..execution.trade_logger import TradeLogger
from ..reporting.line_notifier import LineNotifier
from ..reporting.daily_report import DailyReportGenerator
from .market_hours import MarketHoursManager


def load_settings(config_path: str = "config/settings.yaml") -> dict:
    """設定ファイルを読み込む"""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TradingBot:
    """
    自動取引Botのメインクラス
    全コンポーネントを統合して自動取引サイクルを制御する
    """

    def __init__(self, settings: dict, paper_trading: bool = True):
        self.settings = settings
        self.paper_trading = paper_trading
        self.et_tz = pytz.timezone("America/New_York")
        self._running = False
        self._last_entry_time: Optional[datetime] = None  # 同時多発エントリー防止用
        self._last_disconnect_notify: Optional[datetime] = None  # 接続断通知のクールダウン用
        self._disconnect_count: int = 0  # 連続切断カウント（一時的な切断を無視するため）
        self._risk_init_pending: bool = False  # IBKR接続断でリスクマネジャー初期化が保留中

        logger.info("=" * 60)
        logger.info("US Stock Trading Bot 起動中...")
        mode = "ペーパートレードモード" if paper_trading else "本番取引モード"
        logger.info(f"モード: {mode}")
        logger.info("=" * 60)

        # コンポーネントの初期化
        self.market_hours = MarketHoursManager()
        self.data = MarketDataFetcher()
        self.trade_logger = TradeLogger()
        self.risk_manager = RiskManager(settings)
        self.sizer = PositionSizer(settings)
        self.line = LineNotifier()
        self.universe = UniverseManager(settings, self.data)

        # IBKR クライアント
        ibkr_config = settings["ibkr"]
        self.ibkr = IBKRClient(
            host=os.getenv("IBKR_HOST", ibkr_config["host"]),
            port=int(os.getenv("IBKR_PORT", ibkr_config["port"])),
            client_id=ibkr_config["client_id"],
            paper_trading=paper_trading,
        )

        # 戦略
        self.swing_strategy = SwingTradeStrategy(settings)
        self.day_strategy = DayTradeStrategy(settings)

        # 日次レポートジェネレーター
        self.report_generator = DailyReportGenerator(
            self.trade_logger, self.data, self.universe, settings
        )

        # 注文マネージャー（IBKRが接続後に使用）
        self.order_manager = None

    def start(self):
        """Botのメインループを開始する"""
        self._running = True

        if not self.ibkr.connect():
            logger.error("IBKRへの接続に失敗しました。TWSまたはIB Gatewayを起動してください。")
            self.line.send("⚠️ Bot起動失敗: IBKR接続エラー\nTWSまたはIB Gatewayを確認してください。")
            return

        self.order_manager = OrderManager(
            self.ibkr, self.data, self.sizer,
            self.risk_manager, self.trade_logger, self.settings
        )

        self._reconcile_open_trades()
        logger.info("Bot起動完了。市場オープン待機中...")
        logger.info(self.market_hours.get_market_status_str())

        try:
            self._main_loop()
        except KeyboardInterrupt:
            logger.info("ユーザーによる停止")
        except Exception as e:
            logger.critical(f"予期しないエラー: {e}", exc_info=True)
            self.line.send(f"⚠️ Botエラー: {str(e)[:200]}")
        finally:
            self._shutdown()

    def _main_loop(self):
        """メインループ: 市場の状態に応じてアクションを実行する"""
        market_was_open = False
        daily_report_sent = False

        while self._running:
            now = datetime.now(self.et_tz)
            is_open = self.market_hours.is_market_open()

            # 市場オープン時の処理
            if is_open and not market_was_open:
                self._on_market_open()
                market_was_open = True
                daily_report_sent = False

            # 市場クローズ時の処理
            elif not is_open and market_was_open:
                self._on_market_close()
                market_was_open = False

                if not daily_report_sent:
                    self._send_daily_report()
                    daily_report_sent = True

            # 市場開場中の処理
            if is_open:
                self._during_market_hours()

            # 次のチェックまで待機
            # 開場中は60秒ごと、閉場中は5分ごと
            # ※ time.sleep() ではなく ib.sleep() を使う（重要）:
            #   time.sleep() はib_insyncのイベントループを止めるため、IBKRの
            #   アカウント更新・keepaliveが届かず接続が不安定になり _during_market_hours()
            #   が何分もブロックしてスキャンの:00/:30マークを全て見逃す原因になる。
            #   ib.sleep() はイベントループを動かしながら待機するため安全。
            wait_seconds = 60 if is_open else 300
            logger.debug(f"次のチェックまで{wait_seconds}秒待機...")
            try:
                self.ibkr.ib.sleep(wait_seconds)
            except Exception as e:
                # ib_insync が IBKR接続イベント（Error 1100等）を受信して例外を投げることがある
                # ここでクラッシュするとログが残らずサイレント終了するため必ずキャッチする
                logger.warning(f"ib.sleep中断（IBKRイベント）: {e} → 5秒後に再開")
                time.sleep(5)  # 短いフォールバック（すぐ次のループで再接続処理が動く）

    def _on_market_open(self):
        """市場オープン時の処理"""
        logger.info("🔔 市場オープン - 取引開始")

        # 米国東部時間で現在時刻を取得（週次リセット判定に使用）
        now = datetime.now(self.et_tz)

        # IBKR接続直後はデータ取得に時間がかかるため少し待つ
        time.sleep(5)

        # ポートフォリオ価値を取得（$0の場合は最大3回リトライ）
        portfolio_value = 0
        for attempt in range(3):
            portfolio_value = self.ibkr.get_portfolio_value()
            if portfolio_value > 0:
                break
            logger.warning(f"ポートフォリオ残高$0 リトライ中... ({attempt+1}/3)")
            time.sleep(10)

        if portfolio_value <= 0:
            logger.error("ポートフォリオ残高を取得できません。IB Gatewayの接続を確認してください")
            # $1 の仮値でリスクマネジャーを初期化すると損失上限が$0.02になり
            # 以降の全取引が永続的にブロックされる。接続回復後に再初期化するためスキップ。
            logger.warning("リスクマネジャー初期化をスキップ（IBKR接続回復後に自動再初期化されます）")
            self._risk_init_pending = True  # 再接続後に初期化が必要なフラグ
            portfolio_value = 0
        else:
            self.risk_manager.initialize_day(portfolio_value)
            self._risk_init_pending = False

        # 月曜日（ET）は週次リセット（週次損失上限を正しく機能させるため）
        if now.weekday() == 0:  # 0 = Monday
            self.risk_manager.initialize_week(portfolio_value)
            logger.info(f"週次リセット完了: 週開始ポートフォリオ${portfolio_value:.2f}")
        # 起動直後でstart_of_week_valueが未設定の場合も初期化（途中起動でも週次損失監視を機能させる）
        elif self.risk_manager.start_of_week_value is None:
            self.risk_manager.initialize_week(portfolio_value)
            logger.info(f"週途中起動: 週次損益基準値を${portfolio_value:.2f}で初期化")

        # 市場状態を取得
        market_status = self.data.get_market_status()
        logger.info(
            f"市場状態: {market_status['market_condition']} | "
            f"VIX: {market_status.get('vix', 'N/A')} | "
            f"SPY: {market_status.get('spy_trend', 'N/A')}"
        )

        # LINE通知
        self.line.notify_market_open(market_status)

        # VIXが高い場合は警告
        vix = market_status.get("vix", 20)
        if vix and vix > self.settings["testa_rules"]["market_filter"]["vix_caution"]:
            logger.warning(f"⚠️ VIX={vix:.1f}: 高ボラティリティ環境。ポジションサイズを縮小します")

    def _on_market_close(self):
        """市場クローズ時の処理"""
        logger.info("🔕 市場クローズ")

        # デイトレポジションを強制クローズ
        if self.order_manager:
            self.order_manager.close_all_day_trades()

        self.line.notify_market_close()

    def _during_market_hours(self):
        """市場開場中のメイン処理（毎分実行）"""
        try:
            self._check_risk_status()
            self._sync_ibkr_fills()   # IBKRで自動実行されたストップ注文をDBに反映
            self._monitor_open_positions()
            self._scan_for_signals()
        except Exception as e:
            logger.error(f"取引ループエラー: {e}", exc_info=True)

    def _check_risk_status(self):
        """リスク状態を更新してBotを停止すべきか確認する"""
        if not self.ibkr.is_connected:
            self._disconnect_count += 1
            logger.warning(f"IBKR接続確認失敗 ({self._disconnect_count}回連続)")

            # IB Gatewayは一時的に切断→自動復旧（Error 1100/1102）することがある
            # 3回連続（約3分）で初めて本当の切断と判断して対応する
            if self._disconnect_count < 3:
                return

            # 3回連続で切断 → 通知＆再接続を試みる（30分に1回のみ通知）
            now = datetime.now(self.et_tz)

            # Error 1102（自動復旧）が先に発生して既に再接続済みの場合はスキップ
            # ib_insync が自動でリコネクトするため、ここで connect() を呼ぶと二重接続になる
            if self.ibkr.is_connected:
                logger.info("IBKR自動復旧を確認（Error 1102）。手動再接続はスキップします")
                self._disconnect_count = 0
                return

            if (self._last_disconnect_notify is None or
                    (now - self._last_disconnect_notify).total_seconds() > 1800):
                logger.error("IBKR接続断（3分以上）- 再接続を試みます")
                self.line.notify_connection_lost()
                self._last_disconnect_notify = now

            if not self.ibkr.connect():
                logger.critical("IBKR再接続失敗")
                return

            logger.info("IBKR再接続成功")
            self._disconnect_count = 0
            self._last_disconnect_notify = None

            # 再接続後: 接続断中に初期化が保留されていた場合は正しい残高で再初期化
            if getattr(self, "_risk_init_pending", False):
                time.sleep(3)  # IBKR接続直後はデータが届くまで少し待つ
                pv = self.ibkr.get_portfolio_value()
                if pv > 0:
                    self.risk_manager.initialize_day(pv)
                    if self.risk_manager.start_of_week_value is None:
                        self.risk_manager.initialize_week(pv)
                    self._risk_init_pending = False
                    logger.info(f"リスクマネジャー再初期化完了: ポートフォリオ${pv:,.2f}")
                else:
                    logger.warning("再接続後もポートフォリオ取得失敗。次回ループで再試行します")
            return

        # 接続正常 → カウンターリセット
        self._disconnect_count = 0

        portfolio_value = self.ibkr.get_portfolio_value()
        risk_status = self.risk_manager.update_pnl(portfolio_value)

        if risk_status["action"] == "stop_all":
            logger.critical(f"損失上限到達: {risk_status['pause_reason']}")
            if self.order_manager:
                self.ibkr.close_all_positions()
            self.line.notify_emergency_stop(
                risk_status.get("pause_reason", "損失上限到達"),
                portfolio_value
            )

    def _sync_ibkr_fills(self):
        """
        IBKRで自動実行されたストップ注文の約定をDBに反映する
        ストップ注文が発動してIBKRでポジションがクローズされた場合に、DBを正しく更新する
        """
        db_open = self.trade_logger.get_open_trades()
        if not db_open:
            return

        # 接続断時はスキップ（get_positions()が空リストを返して誤クローズを防ぐ）
        if not self.ibkr.is_connected:
            logger.debug("_sync_ibkr_fills: IBKR未接続のためスキップ")
            return

        ibkr_positions = self.ibkr.get_positions()
        ibkr_symbols = {p["symbol"] for p in ibkr_positions}

        # 安全ガード: IBKRが0件でDBに2件以上オープントレードがある場合は
        # 一時的なデータ取得エラーの可能性が高いためスキップ
        # （全ポジションが同時に消えるケースは通常ない）
        if len(ibkr_positions) == 0 and len(db_open) >= 2:
            logger.warning(
                f"_sync_ibkr_fills: IBKRポジション0件（DBに{len(db_open)}件オープン）"
                " - 一時的なデータエラーの可能性があるためスキップ"
            )
            return

        for trade in db_open:
            symbol = trade["symbol"]
            if symbol in ibkr_symbols:
                continue  # まだIBKRにポジションあり → 正常

            # IBKRにポジションがない = ストップ注文などで約定済みと判断
            current_price = self.data.get_current_price(symbol)
            if current_price is None:
                current_price = trade["entry_price"]

            pnl = (current_price - trade["entry_price"]) * trade["shares"]
            reason = "IBKRストップ注文約定（自動検知）"
            self.trade_logger.log_exit(
                trade_id=trade["id"],
                exit_price=current_price,
                exit_reason=reason,
            )
            logger.info(f"[{symbol}] IBKR約定を検知しDBに反映: PnL=${pnl:.2f}")
            self.line.notify_trade_exit({"symbol": symbol}, pnl, reason)

    def _monitor_open_positions(self):
        """オープンポジションの監視・エグジット実行"""
        if not self.order_manager:
            return

        actions = self.order_manager.monitor_positions()
        for action in actions:
            pnl = action.get("pnl", 0)
            symbol = action.get("symbol", "")
            reason = action.get("exit_reason", "")

            trade_info = {"symbol": symbol}
            self.line.notify_trade_exit(trade_info, pnl, reason)
            logger.info(f"[{symbol}] ポジションクローズ: PnL=${pnl:.2f} ({reason})")

    def _scan_for_signals(self):
        """
        シグナルスキャン: 取引対象銘柄を分析してエントリーシグナルを探す
        5分毎に実行（メインループの60秒待機と組み合わせて実際は数分ごと）
        """
        now = datetime.now(self.et_tz)

        # スイング特化: 30分ごとのスキャン（毎時:00と:30）+ 9:35の初回スキャン
        # 日足データを使うため高頻度スキャン不要。9:35のみ寄り付き直後の特例とする
        # ※ 以前の実装は (now.hour-9)*60+now.minute の計算ミスで12:00/14:00/15:00が機能していなかった
        is_thirty_min_mark = now.minute in (0, 30)
        is_initial_scan = now.hour == 9 and now.minute == 35
        if not (is_thirty_min_mark or is_initial_scan):
            return

        allowed, stop_reason = self.risk_manager.is_trading_allowed()
        if not allowed:
            logger.info(f"スキャンスキップ: Bot停止中 ({stop_reason})")
            return

        # 経済指標イベント日は新規エントリーを抑制
        # （雇用統計・FOMC等で予測不能なボラ急上昇によりストップ難民化を防ぐ）
        is_event_day, event_name = self.market_hours.is_high_volatility_event_day()
        if is_event_day:
            logger.warning(f"⚠️ 高ボライベント日のため新規エントリーをスキップ: {event_name}")
            return

        market_status = self.data.get_market_status()
        tradable_symbols = self.universe.get_tradable_symbols(mode="swing")
        vix = market_status.get("vix", 20)
        volume_multiplier = 0.5 if vix and vix > self.settings["testa_rules"]["market_filter"]["vix_caution"] else 1.0

        max_positions = self.settings["portfolio"]["max_positions"]
        scan_count = 0
        signal_count = 0

        logger.info(
            f"📡 スイングスキャン開始: {len(tradable_symbols)}銘柄 | "
            f"市場: {market_status.get('market_condition','?')} | "
            f"VIX: {vix} | SPY: {market_status.get('spy_trend','?')}"
        )

        # 直前のエントリーから5分以内なら今回のスキャン全体をスキップ
        # （相関の高い銘柄が同時に同方向へ動いてまとめて損切りされるリスクを防ぐ）
        if self._last_entry_time:
            elapsed = (now - self._last_entry_time).total_seconds()
            if elapsed < 300:
                logger.info(f"エントリー間隔制限中（{300 - elapsed:.0f}秒後にスキャン再開）")
                return

        # ポジション上限チェック
        open_trade_count = len(self.trade_logger.get_open_trades())
        if open_trade_count >= max_positions:
            logger.info(f"最大ポジション数到達 ({open_trade_count}/{max_positions})。スキャン省略")
            return

        # フェーズ1: 全銘柄でシグナル候補を収集（実行はしない）
        # 「最初に見つかったシグナル」ではなく「最良シグナル」を選択するため
        candidate_signals = []  # [(signal, trade_type), ...]
        day_trade_enabled = self.settings["day_trade"]["enabled"]

        for symbol in tradable_symbols:
            try:
                scan_count += 1
                # スイングシグナル（日足データ）
                # SMA200計算には200本以上必要なため "1y"（約250本）を取得
                df_daily = self.data.get_historical_data(symbol, period="1y", interval="1d")
                if df_daily is None or len(df_daily) < 55:
                    logger.debug(f"[{symbol}] データ不足のためスキップ ({len(df_daily) if df_daily is not None else 0}件)")
                    continue

                df_daily = self.data.calculate_indicators(df_daily)
                swing_sig = self.swing_strategy.generate_signal(symbol, df_daily, market_status)
                if swing_sig:
                    candidate_signals.append((swing_sig, "swing"))

                # デイトレシグナル（5分足データ） - day_trade.enabled=falseなら内部ガードでNone
                if day_trade_enabled and self.day_strategy.is_tradeable_time():
                    df_intraday = self.data.get_intraday_data(symbol, days=1, interval="5m")
                    if df_intraday is not None and len(df_intraday) >= 30:
                        df_intraday = self.data.calculate_intraday_indicators(df_intraday)
                        day_sig = self.day_strategy.generate_signal(symbol, df_intraday, market_status)
                        if day_sig:
                            candidate_signals.append((day_sig, "day"))

            except Exception as e:
                logger.error(f"[{symbol}] シグナルスキャンエラー: {e}")

        signal_count = len(candidate_signals)
        logger.info(f"📡 スキャン完了: {scan_count}銘柄スキャン | シグナル候補{signal_count}件")

        if not candidate_signals:
            return

        # フェーズ2: 確信度の高い順にソートして最良の1件のみエントリー
        # （5分後のスキャンで次の候補を再評価する → ポジション分散）
        candidate_signals.sort(key=lambda x: x[0].confidence, reverse=True)

        # 上位候補を一覧でログ出力（透明性）
        top_summary = " / ".join(
            f"{s.symbol}({s.strategy_name},{s.confidence:.2f})"
            for s, _ in candidate_signals[:5]
        )
        logger.info(f"🏆 シグナル順位: {top_summary}")

        best_signal, best_trade_type = candidate_signals[0]
        logger.info(
            f"✅ 最良シグナル選択: {best_signal.symbol} ({best_signal.strategy_name}) "
            f"確信度={best_signal.confidence:.2f}"
        )

        result = self.order_manager.execute_signal(
            best_signal, trade_type=best_trade_type, volume_multiplier=volume_multiplier
        )
        if result:
            self._last_entry_time = now
            self.line.notify_trade_entry({
                "symbol": best_signal.symbol,
                "strategy": best_signal.strategy_name,
                "shares": result["shares"],
                "entry_price": best_signal.price,
                "stop_loss": best_signal.stop_loss,
                "take_profit": best_signal.take_profit,
                "signal_reason": best_signal.reason,
            })

    def _send_daily_report(self):
        """日次レポートを生成してLINEに送信する"""
        try:
            portfolio_value = self.ibkr.get_portfolio_value()
            daily_pnl = self.risk_manager.daily_pnl

            report = self.report_generator.generate(portfolio_value, daily_pnl)
            self.line.notify_daily_report(report)
            logger.info("日次レポート送信完了")
        except Exception as e:
            logger.error(f"日次レポート生成エラー: {e}")

    def _shutdown(self):
        """Bot終了処理"""
        logger.info("Bot終了処理中...")
        self._running = False
        if self.ibkr.is_connected:
            self.ibkr.disconnect()
        logger.info("Bot終了完了")

    def _reconcile_open_trades(self):
        """
        起動時にDBのオープントレードとIBKRの実ポジションを照合する
        [パターンA] DB=open、IBKR=なし → キャンセル済みに更新
        [パターンB] DB=closed/cancelled、IBKR=あり → ゴースト逆ポジション → 成行クローズ
        昨日の事故（旧ポジがIBKRに残りmax_positionsをブロック）を防ぐ
        """
        ibkr_positions = self.ibkr.get_positions()
        ibkr_symbols = {pos["symbol"] for pos in ibkr_positions}

        # パターンA: DB=open だが IBKR にない → キャンセル扱い
        db_open = self.trade_logger.get_open_trades()
        cancelled_count = 0
        for trade in db_open:
            symbol = trade["symbol"]
            if symbol not in ibkr_symbols:
                self.trade_logger.mark_as_cancelled(
                    trade["id"], "起動時照合: IBKRにポジションなし（注文キャンセル済みと判断）"
                )
                logger.warning(f"[{symbol}] DB open→cancelled に更新（IBKRにポジションなし）")
                cancelled_count += 1

        # パターンB: IBKR にポジションがあるが DB で管理されていない → ゴースト逆ポジション
        # 昨日の事故: 旧デイトレポジがIBKRに残りmax_positionsをブロックした
        db_open_symbols = {t["symbol"] for t in db_open}
        ghost_closed = 0
        for pos in ibkr_positions:
            symbol = pos["symbol"]
            if symbol not in db_open_symbols:
                qty = abs(int(pos["quantity"]))
                logger.warning(
                    f"[{symbol}] IBKRにゴーストポジション検出（DB管理外）: {qty}株 "
                    f"avg${pos['avg_cost']:.2f} → 成行クローズします"
                )
                result = self.ibkr.close_position(symbol)
                if result:
                    logger.info(f"[{symbol}] ゴーストポジション成行クローズ完了")
                    ghost_closed += 1
                else:
                    logger.error(f"[{symbol}] ゴーストポジションのクローズに失敗しました。手動で確認してください")

        if cancelled_count:
            logger.info(f"起動時照合(A): {cancelled_count}件のDBゴーストトレードをキャンセルに更新")
        if ghost_closed:
            logger.info(f"起動時照合(B): {ghost_closed}件のIBKRゴーストポジションをクローズ")
        if not cancelled_count and not ghost_closed:
            logger.info(f"起動時照合完了: DB({len(db_open)}件)とIBKRのポジションが一致")

    def stop(self):
        """Botを停止する"""
        self._running = False
