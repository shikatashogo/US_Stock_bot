"""
東証 ORB（Opening Range Breakout）バックテスト
=================================================

戦略ルール:
  [レンジ形成]
    - 9:00 寄り付き後、9:00〜9:30（30分）の高値/安値でORBレンジを確定
    - 寄り付き直後（9:00）のみ出来高ゼロのバーが混入する場合があるため除外

  [エントリー]
    - Long : ORBレンジ高値を5分足終値で上抜け
    - Short: ORBレンジ安値を5分足終値で下抜け（一日信用）

  [エグジット]
    - 利確: エントリーからリスク幅 × RR倍（デフォルト2.0）
    - 損切: ORBレンジの反対端
    - 強制決済: 15:20（引け10分前）に未決済ポジションを成行クローズ
    - 昼休み前: 11:25にポジション保有中なら強制決済

  [フィルター]
    - 出来高: ORBレンジ中の出来高が日次平均の1.3倍以上
    - レンジ幅: 前日ATRの20〜150%（狭すぎ・広すぎ除外）
    - 値幅制限: S高/S安まで残り2%以内はエントリー禁止
    - 1日1取引: 1銘柄につき1日1シグナルまで（再エントリーなし）

  [東証固有ルール]
    - 昼休み（11:30〜12:30）中のエントリー禁止
    - 15:20以降のエントリー禁止
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from datetime import date, time
from typing import List, Optional

import numpy as np
import pandas as pd
import pytz
from loguru import logger

from .tse_data_fetcher import TSEDataFetcher, calc_atr, calc_daily_limit

warnings.filterwarnings("ignore")

JST = pytz.timezone("Asia/Tokyo")

# ─── 取引時間定数 ────────────────────────────────────────────
ORB_START = time(9, 0)
ORB_END = time(9, 30)           # ORBレンジ確定時刻
ENTRY_START = time(9, 30)       # エントリー開始（ORB確定後）
LUNCH_CLOSE = time(11, 25)      # 昼休み前の強制決済デッドライン
LUNCH_START = time(11, 30)      # 昼休み開始
LUNCH_END = time(12, 30)        # 昼休み終了
FORCE_CLOSE = time(15, 20)      # 強制決済デッドライン（引け10分前）


# ─── データクラス ─────────────────────────────────────────────
@dataclass
class ORBRange:
    """形成されたORBレンジ"""
    high: float
    low: float
    width: float       # high - low
    width_pct: float   # width / low * 100
    volume: float      # ORB期間の合計出来高


@dataclass
class Trade:
    """1取引の記録"""
    symbol: str
    trade_date: date
    direction: str       # "long" or "short"
    entry_time: pd.Timestamp
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_per_share: float
    shares: int

    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""    # "tp" / "sl" / "force_close" / "lunch_close"

    @property
    def pnl_per_share(self) -> float:
        if self.exit_price is None:
            return 0.0
        if self.direction == "long":
            return self.exit_price - self.entry_price
        else:
            return self.entry_price - self.exit_price

    @property
    def pnl(self) -> float:
        return self.pnl_per_share * self.shares

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0

    @property
    def hold_minutes(self) -> float:
        if self.exit_time is None:
            return 0.0
        return (self.exit_time - self.entry_time).total_seconds() / 60


@dataclass
class BacktestConfig:
    """バックテスト設定"""
    # ORB設定
    orb_minutes: int = 30           # ORBレンジ形成期間（分）
    rr_ratio: float = 2.0           # リスクリワード比

    # 資金設定
    capital: float = 500_000        # 初期資金（円）
    leverage: float = 2.0           # 信用取引レバレッジ
    risk_per_trade_pct: float = 0.005  # 1取引リスク上限（0.5%）
    lot_size: int = 100             # 単元株数

    # フィルター
    min_volume_ratio: float = 0.7   # ORB出来高/比例基準出来高 最小倍率
    # (東証取引時間330分中30分≈9%を基準とした出来高比較)
    min_range_atr_ratio: float = 0.15   # ORBレンジ幅/ATR 最小倍率
    max_range_atr_ratio: float = 2.00   # ORBレンジ幅/ATR 最大倍率
    limit_zone_pct: float = 0.02    # 値幅制限ゾーン（上限/下限から2%以内は禁止）

    # ギャップ方向フィルター（False=現行通り両方向、True=ギャップ方向のみ）
    gap_direction_filter: bool = False
    gap_filter_threshold: float = 0.001  # 中立ギャップとみなす閾値（±0.1%以内は両方向OK）

    # 対象銘柄（¥500K資金・信用取引30%証拠金で1ロット購入可能な銘柄に限定）
    # 除外基準: 1ロット必要証拠金 = 株価 × 100株 × 30% > 資金 ¥500K
    # 除外例: 8035(TEL ¥47K→証拠金¥1.4M)、6861(Keyence ¥75K→¥2.3M)、6367(ダイキン ¥24K→¥714K)
    symbols: List[str] = field(default_factory=lambda: [
        "8306",  # 三菱UFJ  ~¥3,000  証拠金~¥90K
        "6758",  # ソニー   ~¥3,700  証拠金~¥111K
        "7203",  # トヨタ   ~¥2,900  証拠金~¥87K
        "9432",  # NTT     ~¥155   証拠金~¥5K
        "9984",  # ソフトバンクG ~¥5,300 証拠金~¥159K
        "6954",  # ファナック ~¥7,600  証拠金~¥228K
        "4063",  # 信越化学 ~¥6,900  証拠金~¥207K
        "6503",  # 三菱電機 ~¥2,200  証拠金~¥66K
        "9433",  # KDDI    ~¥4,700  証拠金~¥141K
        "8316",  # 三井住友FG ~¥4,200 証拠金~¥126K
    ])


# ─── メインバックテスタ ───────────────────────────────────────
class TSEORBBacktester:
    """
    東証ORB戦略バックテスター

    使い方:
        config = BacktestConfig(capital=500_000, rr_ratio=2.0)
        bt = TSEORBBacktester(config)
        results = bt.run()
        bt.print_summary(results)
    """

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()
        self.fetcher = TSEDataFetcher()
        self.trades: List[Trade] = []

    # ─────────────────────────────────────────────────────────────
    # 公開メソッド
    # ─────────────────────────────────────────────────────────────

    def run(self) -> dict:
        """全銘柄のバックテストを実行して結果を返す"""
        self.trades = []
        cfg = self.config

        logger.info("=== TSE ORB バックテスト開始 ===")
        logger.info(f"銘柄数: {len(cfg.symbols)} | RR: {cfg.rr_ratio} | "
                    f"資金: ¥{cfg.capital:,.0f} | レバレッジ: {cfg.leverage}x")

        # データ取得
        intraday_data = self.fetcher.fetch_universe(cfg.symbols, interval="5m")
        daily_data = self.fetcher.fetch_universe(cfg.symbols, interval="1d")

        for symbol in cfg.symbols:
            if symbol not in intraday_data or symbol not in daily_data:
                logger.warning(f"[{symbol}] データなし → スキップ")
                continue
            symbol_trades = self._backtest_symbol(
                symbol, intraday_data[symbol], daily_data[symbol]
            )
            self.trades.extend(symbol_trades)
            logger.info(f"[{symbol}] {len(symbol_trades)}取引")

        logger.info(f"=== バックテスト完了: 合計{len(self.trades)}取引 ===")
        return self._calc_results()

    def print_summary(self, results: dict) -> None:
        """結果サマリーをコンソール出力"""
        s = results["summary"]
        print("\n" + "=" * 55)
        print("  TSE ORB バックテスト結果")
        print("=" * 55)
        print(f"  期間          : {results['period_start']} 〜 {results['period_end']}")
        print(f"  対象銘柄      : {', '.join(self.config.symbols)}")
        print(f"  総取引数      : {s['total_trades']} 回")
        print(f"  勝率          : {s['win_rate']:.1f}%")
        print(f"  勝ち/負け     : {s['winning_trades']}回 / {s['losing_trades']}回")
        print("-" * 55)
        print(f"  純損益        : ¥{s['total_pnl']:,.0f}")
        print(f"  平均勝ち      : ¥{s['avg_win']:,.0f}")
        print(f"  平均負け      : ¥{s['avg_loss']:,.0f}")
        print(f"  損益比        : {s['profit_factor']:.2f}")
        print(f"  期待値/取引   : ¥{s['expectancy']:,.0f}")
        print("-" * 55)
        print(f"  最大ドローダウン: ¥{s['max_drawdown']:,.0f} "
              f"({s['max_drawdown_pct']:.1f}%)")
        print(f"  平均保有時間  : {s['avg_hold_minutes']:.0f}分")
        print("-" * 55)
        print("  エグジット理由内訳:")
        for reason, count in s["exit_reasons"].items():
            print(f"    {reason:15s}: {count}回")
        print("=" * 55)

        if results.get("monthly"):
            print("\n  月次損益:")
            for ym, pnl in sorted(results["monthly"].items()):
                bar = "█" * int(abs(pnl) / 5000) if abs(pnl) > 0 else ""
                sign = "+" if pnl >= 0 else ""
                print(f"    {ym}: {sign}¥{pnl:,.0f}  {bar}")
        print()

    # ─────────────────────────────────────────────────────────────
    # 銘柄別バックテスト
    # ─────────────────────────────────────────────────────────────

    def _backtest_symbol(
        self, symbol: str, df5: pd.DataFrame, df_daily: pd.DataFrame
    ) -> List[Trade]:
        """1銘柄のバックテストを実行"""
        cfg = self.config
        trades = []

        # ATR計算（日足ベース）
        atr_series = calc_atr(df_daily, period=14)

        # 取引日ごとに処理
        trading_dates = df5.index.normalize().unique()

        for trade_date in trading_dates:
            day_df = df5[df5.index.normalize() == trade_date]
            if len(day_df) < 6:  # 最低6本（30分）必要
                continue

            # 当日の日足ATR・前日終値を取得
            daily_before = df_daily[df_daily.index.normalize() < trade_date]
            if len(daily_before) < 15:
                continue  # ATR計算に最低15日必要
            prev_close = daily_before["close"].iloc[-1]
            today_atr = atr_series.loc[daily_before.index[-1]]

            # 当日の値幅制限
            limit_low, limit_high = calc_daily_limit(prev_close)

            # 平均出来高（日足5日平均をORB期間比率で推定）
            avg_daily_vol = df_daily["volume"].iloc[-20:].mean() if len(df_daily) >= 20 else df_daily["volume"].mean()

            # ORBレンジ計算
            orb = self._calc_orb_range(day_df)
            if orb is None:
                continue

            # フィルター適用
            skip_reason = self._apply_filters(
                orb, today_atr, avg_daily_vol, limit_low, limit_high, prev_close
            )
            if skip_reason:
                logger.debug(f"[{symbol}][{trade_date.date()}] スキップ: {skip_reason}")
                continue

            # ギャップ方向フィルター: 寄り付き価格 vs 前日終値
            allowed_direction = "both"
            if cfg.gap_direction_filter:
                day_open = day_df.iloc[0]["open"]
                gap_pct = (day_open - prev_close) / prev_close if prev_close > 0 else 0.0
                if gap_pct > cfg.gap_filter_threshold:
                    allowed_direction = "long"   # 上ギャップ→ロングのみ
                elif gap_pct < -cfg.gap_filter_threshold:
                    allowed_direction = "short"  # 下ギャップ→ショートのみ
                # |gap| <= threshold → "both"（中立ギャップ、フィルターなし）

            # シグナル検出とトレード実行
            trade = self._simulate_trade(
                symbol, trade_date, day_df, orb,
                limit_low, limit_high, prev_close,
                allowed_direction=allowed_direction,
            )
            if trade:
                trades.append(trade)

        return trades

    # ─────────────────────────────────────────────────────────────
    # ORBレンジ計算
    # ─────────────────────────────────────────────────────────────

    def _calc_orb_range(self, day_df: pd.DataFrame) -> Optional[ORBRange]:
        """
        9:00〜9:30のORBレンジを計算する
        注意: 寄り付き直後のVolume=0バーは除外済み（データクリーニング済み）
        """
        orb_data = day_df.between_time(
            ORB_START.strftime("%H:%M"),
            (pd.Timestamp.combine(
                day_df.index[0].date(), ORB_END
            ) - pd.Timedelta(minutes=1)).strftime("%H:%M")
        )

        if len(orb_data) < 3:  # 最低3本（15分）のデータが必要
            return None

        orb_high = orb_data["high"].max()
        orb_low = orb_data["low"].min()
        orb_vol = orb_data["volume"].sum()
        width = orb_high - orb_low

        if orb_low <= 0 or width <= 0:
            return None

        return ORBRange(
            high=orb_high,
            low=orb_low,
            width=width,
            width_pct=width / orb_low * 100,
            volume=orb_vol,
        )

    # ─────────────────────────────────────────────────────────────
    # フィルター適用
    # ─────────────────────────────────────────────────────────────

    def _apply_filters(
        self,
        orb: ORBRange,
        atr: float,
        avg_daily_vol: float,
        limit_low: float,
        limit_high: float,
        prev_close: float,
    ) -> Optional[str]:
        """
        フィルターを適用。スキップ理由（str）を返す。問題なければNone。
        """
        cfg = self.config

        # 1. ATRベースのレンジ幅フィルター（狭すぎ・広すぎ除外）
        if atr > 0:
            range_atr_ratio = orb.width / atr
            if range_atr_ratio < cfg.min_range_atr_ratio:
                return f"レンジ幅狭すぎ({range_atr_ratio:.2f}x ATR)"
            if range_atr_ratio > cfg.max_range_atr_ratio:
                return f"レンジ幅広すぎ({range_atr_ratio:.2f}x ATR)"

        # 2. 出来高フィルター（ORB期間出来高 vs 日次平均の比例推定値）
        # 東証取引時間: 330分（前場150分+後場180分）
        # ORB30分 = 330分の約9% → 日次平均の9%が比例基準
        # 寄り付き直後は出来高集中するため1.3倍程度が自然な値
        expected_orb_vol = avg_daily_vol * 0.09
        if expected_orb_vol > 0:
            vol_ratio = orb.volume / expected_orb_vol
            if vol_ratio < cfg.min_volume_ratio:
                return f"出来高不足({vol_ratio:.2f}x, 基準の{cfg.min_volume_ratio}倍未満)"

        # 3. 値幅制限ゾーンフィルター（S高/S安付近はスキップ）
        upper_danger = limit_high * (1 - cfg.limit_zone_pct)
        lower_danger = limit_low * (1 + cfg.limit_zone_pct)
        if orb.high >= upper_danger:
            return f"S高付近({orb.high:.0f} >= {upper_danger:.0f})"
        if orb.low <= lower_danger:
            return f"S安付近({orb.low:.0f} <= {lower_danger:.0f})"

        return None  # フィルター通過

    # ─────────────────────────────────────────────────────────────
    # トレードシミュレーション
    # ─────────────────────────────────────────────────────────────

    def _simulate_trade(
        self,
        symbol: str,
        trade_date,
        day_df: pd.DataFrame,
        orb: ORBRange,
        limit_low: float,
        limit_high: float,
        prev_close: float,
        allowed_direction: str = "both",   # "long" / "short" / "both"
    ) -> Optional[Trade]:
        """
        1日分の5分足データを逐次処理し、ORBブレイクアウトシグナルを検出して取引をシミュレートする
        """
        cfg = self.config

        # ORB確定後のデータ（9:30以降）
        post_orb = day_df.between_time(
            ENTRY_START.strftime("%H:%M"), "15:29"
        )

        if post_orb.empty:
            return None

        position = None  # まだポジションなし

        for ts, row in post_orb.iterrows():
            bar_time = ts.time()

            # ── ポジション保有中の処理 ──────────────────────────────
            if position is not None:
                # 昼休み前強制決済（11:25）
                if bar_time >= LUNCH_CLOSE and bar_time < LUNCH_START:
                    position.exit_time = ts
                    position.exit_price = row["open"]  # 次バーオープンで決済想定
                    position.exit_reason = "lunch_close"
                    return position

                # 昼休み中はスキップ
                if LUNCH_START <= bar_time < LUNCH_END:
                    continue

                # 引け前強制決済（15:20）
                if bar_time >= FORCE_CLOSE:
                    position.exit_time = ts
                    position.exit_price = row["open"]
                    position.exit_reason = "force_close"
                    return position

                if position.direction == "long":
                    # SL優先: 同一バーでTP/SL両方到達した場合は最悪ケース（SL）を採用
                    if row["low"] <= position.stop_loss:
                        position.exit_time = ts
                        position.exit_price = position.stop_loss
                        position.exit_reason = "sl"
                        return position
                    if row["high"] >= position.take_profit:
                        position.exit_time = ts
                        position.exit_price = position.take_profit
                        position.exit_reason = "tp"
                        return position

                else:  # short
                    # SL優先（同上）
                    if row["high"] >= position.stop_loss:
                        position.exit_time = ts
                        position.exit_price = position.stop_loss
                        position.exit_reason = "sl"
                        return position
                    if row["low"] <= position.take_profit:
                        position.exit_time = ts
                        position.exit_price = position.take_profit
                        position.exit_reason = "tp"
                        return position

                continue  # ポジション中は新規シグナル不要

            # ── エントリー判定（ポジションなし）─────────────────────
            # 昼休み中はエントリー禁止
            if LUNCH_START <= bar_time < LUNCH_END:
                continue
            # 15:20以降はエントリー禁止
            if bar_time >= FORCE_CLOSE:
                break

            entry_price = None
            direction = None
            stop_loss = None
            take_profit = None

            close_price = row["close"]

            # Long: ORBレンジ高値を終値で上抜け（ギャップ方向フィルター適用）
            if close_price > orb.high and allowed_direction != "short":
                entry_price = orb.high  # ブレイク後の次バーオープンと仮定（保守的）
                direction = "long"
                stop_loss = orb.low
                risk = entry_price - stop_loss
                take_profit = entry_price + risk * cfg.rr_ratio

                # 値幅上限を超えたTPはキャップ
                if take_profit > limit_high:
                    take_profit = limit_high

            # Short: ORBレンジ安値を終値で下抜け（ギャップ方向フィルター適用）
            elif close_price < orb.low and allowed_direction != "long":
                entry_price = orb.low
                direction = "short"
                stop_loss = orb.high
                risk = stop_loss - entry_price
                take_profit = entry_price - risk * cfg.rr_ratio

                # 値幅下限を超えたTPはキャップ
                if take_profit < limit_low:
                    take_profit = limit_low

            if entry_price is None:
                continue

            # RR比が設定値を下回る場合はスキップ（TP調整後）
            actual_risk = abs(entry_price - stop_loss)
            actual_reward = abs(take_profit - entry_price)
            if actual_risk <= 0 or actual_reward / actual_risk < cfg.rr_ratio * 0.8:
                logger.debug(f"[{symbol}] RR比不足でスキップ")
                continue

            # ── ポジションサイズ計算 ────────────────────────────────
            # 一日信用取引: 委託保証金率30%（最低水準）
            margin_rate = 0.30
            lot_value = cfg.lot_size * entry_price
            required_margin_per_lot = lot_value * margin_rate

            # Bug Fix: 1ロット証拠金が資金を超える場合は取引不可（現実に即した制約）
            if required_margin_per_lot > cfg.capital:
                logger.debug(
                    f"[{symbol}] 1ロット証拠金不足: "
                    f"必要¥{required_margin_per_lot:,.0f} > 資金¥{cfg.capital:,.0f}"
                )
                continue

            # リスクベースでロット数を決定
            # risk_budget = 資金 × レバレッジ × リスク率
            # 例) ¥500K × 2x × 0.5% = ¥5,000リスク予算
            risk_budget = cfg.capital * cfg.leverage * cfg.risk_per_trade_pct
            shares_by_risk = (int(risk_budget / actual_risk) // cfg.lot_size) * cfg.lot_size
            shares = max(cfg.lot_size, shares_by_risk)  # 最低1ロット（証拠金確認済み）

            # 最大ポジション上限: 実効買付余力（capital × leverage）を超えない
            # Bug Fix: max(lot_size, 0) = lot_size という誤りを排除し、純粋に上限を計算
            max_buying_power = cfg.capital * cfg.leverage
            max_shares_by_power = (int(max_buying_power / entry_price) // cfg.lot_size) * cfg.lot_size
            if max_shares_by_power > 0:
                shares = min(shares, max_shares_by_power)

            if shares <= 0:
                continue

            position = Trade(
                symbol=symbol,
                trade_date=trade_date.date() if hasattr(trade_date, "date") else trade_date,
                direction=direction,
                entry_time=ts,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_per_share=actual_risk,
                shares=shares,
            )

        # 最終バーまでポジション未決済（当日データ不完全）
        if position is not None and position.exit_time is None:
            last_bar = post_orb.iloc[-1]
            position.exit_time = post_orb.index[-1]
            position.exit_price = last_bar["close"]
            position.exit_reason = "force_close"
            return position

        return None

    # ─────────────────────────────────────────────────────────────
    # 結果集計
    # ─────────────────────────────────────────────────────────────

    def _calc_results(self) -> dict:
        """取引履歴からパフォーマンス指標を計算する"""
        trades = self.trades
        cfg = self.config

        if not trades:
            return {"summary": {}, "trades": [], "monthly": {}}

        df = pd.DataFrame([{
            "symbol": t.symbol,
            "date": t.trade_date,
            "direction": t.direction,
            "entry_time": t.entry_time,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "exit_reason": t.exit_reason,
            "pnl": t.pnl,
            "pnl_per_share": t.pnl_per_share,
            "shares": t.shares,
            "hold_minutes": t.hold_minutes,
            "is_winner": t.is_winner,
        } for t in trades])

        total = len(df)
        wins = df["is_winner"].sum()
        losses = total - wins

        gross_profit = df.loc[df["pnl"] > 0, "pnl"].sum()
        gross_loss = df.loc[df["pnl"] < 0, "pnl"].sum()
        total_pnl = df["pnl"].sum()

        profit_factor = (
            abs(gross_profit / gross_loss) if gross_loss != 0 else float("inf")
        )
        avg_win = df.loc[df["pnl"] > 0, "pnl"].mean() if wins > 0 else 0
        avg_loss = df.loc[df["pnl"] < 0, "pnl"].mean() if losses > 0 else 0
        expectancy = total_pnl / total if total > 0 else 0

        # 最大ドローダウン計算
        cumulative = df["pnl"].cumsum()
        rolling_max = cumulative.cummax()
        drawdown = cumulative - rolling_max
        max_drawdown = drawdown.min()
        max_drawdown_pct = (
            abs(max_drawdown) / cfg.capital * 100 if cfg.capital > 0 else 0
        )

        # エグジット理由集計
        exit_reasons = df["exit_reason"].value_counts().to_dict()

        # 月次損益
        df["month"] = pd.to_datetime(df["date"]).dt.to_period("M").astype(str)
        monthly = df.groupby("month")["pnl"].sum().to_dict()

        period_start = df["date"].min()
        period_end = df["date"].max()

        summary = {
            "total_trades": total,
            "winning_trades": int(wins),
            "losing_trades": int(losses),
            "win_rate": wins / total * 100 if total > 0 else 0,
            "total_pnl": round(total_pnl, 0),
            "gross_profit": round(gross_profit, 0),
            "gross_loss": round(gross_loss, 0),
            "profit_factor": round(profit_factor, 2),
            "avg_win": round(avg_win, 0),
            "avg_loss": round(avg_loss, 0),
            "expectancy": round(expectancy, 0),
            "max_drawdown": round(abs(max_drawdown), 0),
            "max_drawdown_pct": round(max_drawdown_pct, 1),
            "avg_hold_minutes": round(df["hold_minutes"].mean(), 0),
            "exit_reasons": exit_reasons,
        }

        return {
            "summary": summary,
            "trades": df.to_dict("records"),
            "monthly": monthly,
            "period_start": period_start,
            "period_end": period_end,
        }

    def export_trades_csv(self, results: dict, path: str = "tse_orb_trades.csv") -> None:
        """取引履歴をCSVに出力"""
        if not results.get("trades"):
            logger.warning("出力するトレードデータがありません")
            return
        df = pd.DataFrame(results["trades"])
        df.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info(f"取引履歴をCSV出力: {path}")
