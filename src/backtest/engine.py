"""
バックテストエンジン
過去データを使って戦略を検証する
「再現性があるか・リスクが最小化できているか」を数値で確認する
"""
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import pandas as pd
import numpy as np
from loguru import logger

from ..data.market_data import MarketDataFetcher
from ..strategies.swing_trade import SwingTradeStrategy
from ..strategies.base_strategy import SignalType
from .metrics import BacktestMetrics


class BacktestEngine:
    """戦略のバックテストを実行するエンジン"""

    def __init__(self, settings: dict):
        self.settings = settings
        self.bt_config = settings["backtest"]
        self.data = MarketDataFetcher()
        self.metrics_calc = BacktestMetrics()

        self.initial_capital = self.bt_config["initial_capital"]
        self.commission = self.bt_config["commission_per_share"]
        self.slippage = self.bt_config["slippage_pct"]

    def run_swing_backtest(
        self,
        symbol: str,
        start_date: str = None,
        end_date: str = None,
    ) -> Dict:
        """
        スイングトレード戦略のバックテストを実行する
        """
        start_date = start_date or self.bt_config["start_date"]
        end_date = end_date or self.bt_config["end_date"]

        logger.info(f"バックテスト開始: {symbol} ({start_date} 〜 {end_date})")

        # 開始日の200日前から取得（MA200計算のためのウォームアップ期間）
        from datetime import timedelta
        warmup_start = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=300)).strftime("%Y-%m-%d")
        df_full = self.data.get_historical_data_by_dates(symbol, warmup_start, end_date, interval="1d")

        if df_full is None or len(df_full) < 60:
            logger.error(f"{symbol}: バックテスト用データが不足しています")
            return {"error": "データ不足"}

        df_full.index = pd.to_datetime(df_full.index)

        # テクニカル指標を全データに対して一度だけ計算（O(N²)→O(N)最適化）
        # ローリング計算なので各バーの値はそのバーより前のデータのみで確定している
        df_full = self.data.calculate_indicators(df_full)

        strategy = SwingTradeStrategy(self.settings)
        trades = []
        portfolio_value = self.initial_capital
        portfolio_history = []
        in_position = False
        position = {}

        # バックテスト本番開始インデックスを特定（start_date以降）
        sim_start_idx = 0
        for idx, dt in enumerate(df_full.index):
            if str(dt.date()) >= start_date:
                sim_start_idx = idx
                break
        sim_start_idx = max(sim_start_idx, 55)  # 最低55本は指標計算に必要

        # ウィンドウをスライドしながらシグナルをシミュレーション
        for i in range(sim_start_idx, len(df_full)):
            # 事前計算済みのDataFrameをスライスするだけ（コピー・再計算不要）
            window = df_full.iloc[:i + 1]
            current_bar = df_full.iloc[i]
            current_date = df_full.index[i]

            # 市場状態（バックテストでは簡略化）
            market_status = self._get_simulated_market_status(window)

            if not in_position:
                # エントリーシグナル確認
                signal = strategy.generate_signal(symbol, window, market_status)

                if signal and signal.signal_type == SignalType.BUY:
                    # スリッページを考慮したエントリー価格
                    entry_price = signal.price * (1 + self.slippage)

                    # ポジションサイズ計算（簡略版）
                    risk_per_share = entry_price - signal.stop_loss
                    if risk_per_share <= 0:
                        continue

                    risk_amount = portfolio_value * 0.02  # 2%リスク
                    shares = max(1, int(risk_amount / risk_per_share))
                    max_shares = int(portfolio_value * 0.30 / entry_price)
                    shares = min(shares, max_shares)

                    commission = shares * self.commission
                    cost = shares * entry_price + commission

                    if cost > portfolio_value:
                        continue

                    position = {
                        "entry_date": current_date,
                        "entry_price": entry_price,
                        "stop_loss": signal.stop_loss,
                        "take_profit": signal.take_profit,
                        "shares": shares,
                        "strategy": signal.strategy_name,
                        "entry_reason": signal.reason,
                    }
                    in_position = True

            else:
                # エグジット条件チェック
                low_price = current_bar["low"]
                high_price = current_bar["high"]
                close_price = current_bar["close"]

                exit_price = None
                exit_reason = None

                # 損切り（当日の安値がストップラインを割ったか）
                if low_price <= position["stop_loss"]:
                    # スリッページ: 損切り時は目標より不利な方向（さらに低い価格）で約定
                    exit_price = position["stop_loss"] * (1 - self.slippage)
                    exit_reason = "stop_loss"

                # 利確
                elif high_price >= position["take_profit"]:
                    # スリッページ: 利確時も不利な方向（目標より低い価格）で約定
                    # 修正前: * (1 + slippage) は「目標より高く売れる」という非現実的な想定だった
                    exit_price = position["take_profit"] * (1 - self.slippage)
                    exit_reason = "take_profit"

                # 最大保有日数
                elif (current_date - position["entry_date"]).days >= self.settings["swing_trade"]["hold_days_max"]:
                    exit_price = close_price
                    exit_reason = "max_hold_days"

                if exit_price:
                    shares = position["shares"]
                    pnl = (exit_price - position["entry_price"]) * shares
                    pnl -= shares * self.commission  # 往復手数料

                    portfolio_value += pnl

                    trades.append({
                        "symbol": symbol,
                        "entry_date": position["entry_date"].strftime("%Y-%m-%d"),
                        "exit_date": current_date.strftime("%Y-%m-%d"),
                        "entry_price": round(position["entry_price"], 2),
                        "exit_price": round(exit_price, 2),
                        "shares": shares,
                        "pnl": round(pnl, 2),
                        "pnl_pct": round(pnl / (position["entry_price"] * shares) * 100, 2),
                        "hold_days": (current_date - position["entry_date"]).days,
                        "exit_reason": exit_reason,
                        "strategy": position["strategy"],
                    })

                    in_position = False
                    position = {}

            portfolio_history.append({
                "date": current_date,
                "portfolio_value": portfolio_value,
            })

        # バックテスト終了時に未クローズのポジションを終値で決済
        if in_position and position:
            last_bar = df_full.iloc[-1]
            exit_price = last_bar["close"]
            shares = position["shares"]
            pnl = (exit_price - position["entry_price"]) * shares - shares * self.commission
            portfolio_value += pnl
            trades.append({
                "symbol": symbol,
                "entry_date": position["entry_date"].strftime("%Y-%m-%d"),
                "exit_date": df_full.index[-1].strftime("%Y-%m-%d"),
                "entry_price": round(position["entry_price"], 2),
                "exit_price": round(exit_price, 2),
                "shares": shares,
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl / (position["entry_price"] * shares) * 100, 2),
                "hold_days": (df_full.index[-1] - position["entry_date"]).days,
                "exit_reason": "backtest_end",
                "strategy": position["strategy"],
            })

        # メトリクス計算
        results = self.metrics_calc.calculate(
            trades=trades,
            portfolio_history=portfolio_history,
            initial_capital=self.initial_capital,
            symbol=symbol,
        )

        logger.info(
            f"バックテスト完了: {symbol} | "
            f"取引数: {len(trades)} | "
            f"勝率: {results.get('win_rate', 0):.1f}% | "
            f"総損益: ${results.get('total_pnl', 0):.2f}"
        )

        return results

    def run_multi_symbol_backtest(self, symbols: List[str]) -> Dict:
        """複数銘柄でのバックテストを実行してポートフォリオ全体のパフォーマンスを計算する"""
        all_results = {}
        combined_trades = []

        for symbol in symbols:
            result = self.run_swing_backtest(symbol)
            all_results[symbol] = result
            if "trades" in result:
                combined_trades.extend(result["trades"])

        if not combined_trades:
            return {"error": "取引データなし", "individual": all_results}

        # 全体サマリー
        pnls = [t["pnl"] for t in combined_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        summary = {
            "symbols": symbols,
            "total_trades": len(combined_trades),
            "total_pnl": round(sum(pnls), 2),
            "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
            "profit_factor": round(abs(sum(wins)) / abs(sum(losses)), 2) if losses else float("inf"),
            "individual": all_results,
        }

        return summary

    def _get_simulated_market_status(self, df: pd.DataFrame) -> Dict:
        """バックテスト用の簡略化した市場状態を返す"""
        last = df.iloc[-1]
        ema20 = last.get("ema20", last["close"])
        ema50 = last.get("ema50", last["close"])

        if last["close"] > ema20 > ema50:
            trend = "up"
        elif last["close"] < ema20 < ema50:
            trend = "down"
        else:
            trend = "neutral"

        return {
            "spy_trend": trend,
            "vix": 18,  # バックテストではVIXを固定（保守的な値）
            "market_condition": "bullish" if trend == "up" else "neutral",
        }
