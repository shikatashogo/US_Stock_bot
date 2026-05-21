"""
GF戦略（Gap × First-5min Momentum）バックテスト
=================================================

戦略ルール:
  [エントリーシグナル]
    Step1: ギャップフィルター
      - 当日始値（寄り付き）と前日終値の乖離（ギャップ率）を計算
      - ギャップ率 > +min_gap_pct → Long候補
      - ギャップ率 < -min_gap_pct → Short候補
      - 乖離が小さい日は方向感なし → スキップ

    Step2: 初動確認（第1本足 9:00〜9:05）
      - Long候補: 9:00〜9:05の終値 > 始値（寄り付き後も上昇継続）→ 確認
      - Short候補: 9:00〜9:05の終値 < 始値（下落継続）→ 確認
      - 逆方向（ギャップフェード）は見送り

  [エントリー]
    - 9:05（第1本足確定直後）にエントリー
    - 価格: 第1本足の終値（翌バー始値の近似）
    - 方向: ギャップ方向と一致

  [エグジット: ATRトレイリングストップ]
    - 初期SL = 第1本足の安値（Long）/ 高値（Short）
    - ポジションが有利方向に動くたびに SL を追随させる
      trail_stop = max(trail_stop, peak_price - ATR × trail_factor) [Long]
      trail_stop = min(trail_stop, peak_price + ATR × trail_factor) [Short]
    - SLに到達したら決済
    - 固定TPなし → 強いトレンド日は長く乗れる

  [強制決済]
    - 11:25 昼休み前
    - 15:20 引け前

  [フィルター（スキップ条件）]
    - ギャップ幅 > ATR × max_gap_atr_ratio → 過大ギャップ（反転リスク大）
    - 初期SL幅 > ATR × max_sl_atr_ratio → リスクが大きすぎる
    - 値幅制限ゾーン（S高/S安から2%以内）
    - 1ロット証拠金 > 資金

ORBとの主な違い:
  ORB  → 30分待機 → 固定RR決済 → 前場80%強制クローズ
  GF   → 5分で判断 → トレイリング → 強い日はTPなしで走り続ける
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from datetime import time
from typing import Dict, List, Optional

import pandas as pd
from loguru import logger

from .tse_data_fetcher import TSEDataFetcher, calc_atr, calc_daily_limit

warnings.filterwarnings("ignore")

# ─── 取引時間定数 ────────────────────────────────────────────────
FIRST_BAR_START = time(9, 0)
FIRST_BAR_END   = time(9, 4)    # 9:00バーのみ取得（between_time の上限）
LUNCH_CLOSE     = time(11, 25)
LUNCH_START     = time(11, 30)
LUNCH_END       = time(12, 30)
FORCE_CLOSE     = time(15, 20)


# ─── データクラス ────────────────────────────────────────────────

@dataclass
class GFConfig:
    """GF戦略設定"""
    # エントリーフィルター
    min_gap_pct: float     = 0.005   # 最小ギャップ率（0.5%）
    max_gap_atr_ratio: float = 1.5   # ギャップ幅のATR上限倍率（過大ギャップ除外）
    max_sl_atr_ratio: float  = 0.6   # SL幅のATR上限倍率（リスク大きすぎ除外）
    limit_zone_pct: float    = 0.02  # 値幅制限ゾーン（上下2%以内はスキップ）

    # エグジット
    trail_atr_factor: float  = 0.5   # トレイリングストップ幅（ATR × この係数）

    # 資金設定
    capital: float           = 500_000
    leverage: float          = 2.0
    risk_per_trade_pct: float = 0.005  # 1取引リスク0.5%
    lot_size: int            = 100

    # 対象銘柄
    symbols: List[str] = field(default_factory=lambda: [
        "8306",  # 三菱UFJ
        "6758",  # ソニーG
        "7203",  # トヨタ
        "9432",  # NTT
        "9984",  # ソフトバンクG
        "6954",  # ファナック
        "4063",  # 信越化学
        "6503",  # 三菱電機
        "9433",  # KDDI
        "8316",  # 三井住友FG
    ])


@dataclass
class GFTrade:
    """1取引の記録"""
    symbol: str
    trade_date: object
    direction: str           # "long" / "short"
    entry_time: pd.Timestamp
    entry_price: float
    initial_sl: float        # 初期SL（第1本足の逆端）
    shares: int
    gap_pct: float           # エントリー時のギャップ率（%）
    atr: float               # 当日ATR

    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""    # "trail_sl" / "lunch_close" / "force_close"
    peak_price: float = 0.0  # ポジション保有中の最高値/最安値

    @property
    def pnl_per_share(self) -> float:
        if self.exit_price is None:
            return 0.0
        return (
            self.exit_price - self.entry_price if self.direction == "long"
            else self.entry_price - self.exit_price
        )

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

    @property
    def max_favorable_excursion_pct(self) -> float:
        """ピーク時の含み益率（エントリー比）"""
        if self.direction == "long":
            return (self.peak_price - self.entry_price) / self.entry_price * 100
        else:
            return (self.entry_price - self.peak_price) / self.entry_price * 100


# ─── メインバックテスタ ──────────────────────────────────────────

class GFBacktester:
    """
    GF戦略（Gap × First-5min Momentum）バックテスター

    使い方:
        cfg = GFConfig(capital=500_000, min_gap_pct=0.005)
        bt = GFBacktester(cfg)
        results = bt.run()
        bt.print_summary(results)
    """

    def __init__(self, config: Optional[GFConfig] = None):
        self.config = config or GFConfig()
        self.fetcher = TSEDataFetcher()
        self.trades: List[GFTrade] = []

    # ─────────────────────────────────────────────────────────────
    # 公開メソッド
    # ─────────────────────────────────────────────────────────────

    def run(
        self,
        intraday_data: Optional[Dict[str, pd.DataFrame]] = None,
        daily_data: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> dict:
        """全銘柄のGFバックテストを実行"""
        self.trades = []
        cfg = self.config

        logger.info("=== GF戦略（Gap×First-5min）バックテスト開始 ===")
        logger.info(
            f"ギャップ閾値:{cfg.min_gap_pct*100:.1f}% | "
            f"トレイル:{cfg.trail_atr_factor}×ATR | "
            f"銘柄数:{len(cfg.symbols)}"
        )

        # データ取得（外部から渡されない場合は自分で取得）
        if intraday_data is None:
            intraday_data = self.fetcher.fetch_universe(cfg.symbols, interval="5m")
        if daily_data is None:
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

        logger.info(f"=== GFバックテスト完了: 合計{len(self.trades)}取引 ===")
        return self._calc_results()

    def print_summary(self, results: dict) -> None:
        """結果サマリーをコンソール出力"""
        s = results.get("summary", {})
        if not s:
            print("  取引なし")
            return

        print("\n" + "=" * 58)
        print("  GF戦略（Gap × First-5Min Momentum）結果")
        print("=" * 58)
        print(f"  期間          : {results.get('period_start')} 〜 {results.get('period_end')}")
        print(f"  対象銘柄      : {', '.join(self.config.symbols)}")
        print(f"  総取引数      : {s['total_trades']} 回")
        print(f"  勝率          : {s['win_rate']:.1f}%")
        print(f"  勝ち/負け     : {s['winning_trades']}回 / {s['losing_trades']}回")
        print("-" * 58)
        print(f"  純損益        : ¥{s['total_pnl']:,.0f}")
        print(f"  平均勝ち      : ¥{s['avg_win']:,.0f}")
        print(f"  平均負け      : ¥{s['avg_loss']:,.0f}")
        print(f"  損益比（PF）  : {s['profit_factor']:.2f}")
        print(f"  期待値/取引   : ¥{s['expectancy']:,.0f}")
        print("-" * 58)
        print(f"  最大DD        : ¥{s['max_drawdown']:,.0f} ({s['max_drawdown_pct']:.1f}%)")
        print(f"  平均保有時間  : {s['avg_hold_minutes']:.0f}分")
        print(f"  平均ギャップ  : {s['avg_gap_pct']:.2f}%")
        print(f"  Long/Short比  : {s['long_count']}L / {s['short_count']}S")
        print("-" * 58)
        print("  エグジット理由内訳:")
        total = s["total_trades"]
        for reason, count in sorted(s["exit_reasons"].items(), key=lambda x: -x[1]):
            print(f"    {reason:15s}: {count:>4}回 ({count/total*100:>5.1f}%)")
        print("=" * 58)

        if results.get("monthly"):
            print("\n  月次損益:")
            for ym, pnl in sorted(results["monthly"].items()):
                bar = "█" * min(int(abs(pnl) / 8000), 20)
                sign = "+" if pnl >= 0 else ""
                print(f"    {ym}: {sign}¥{pnl:,.0f}  {bar}")
        print()

    # ─────────────────────────────────────────────────────────────
    # 銘柄別バックテスト
    # ─────────────────────────────────────────────────────────────

    def _backtest_symbol(
        self, symbol: str, df5: pd.DataFrame, df_daily: pd.DataFrame
    ) -> List[GFTrade]:
        """1銘柄のGFバックテストを実行"""
        cfg = self.config
        trades = []
        atr_series = calc_atr(df_daily, period=14)

        for trade_date in df5.index.normalize().unique():
            day_df = df5[df5.index.normalize() == trade_date]
            if len(day_df) < 4:
                continue

            # 前日データ
            daily_before = df_daily[df_daily.index.normalize() < trade_date]
            if len(daily_before) < 15:
                continue

            prev_close = daily_before["close"].iloc[-1]
            today_atr  = float(atr_series.loc[daily_before.index[-1]])
            limit_low, limit_high = calc_daily_limit(prev_close)

            # ── Step1: 第1本足（9:00〜9:05）を取得 ──────────────────
            first_bars = day_df.between_time("09:00", "09:04")
            if first_bars.empty:
                continue

            fb = first_bars.iloc[0]              # 9:00バー
            fb_open  = fb["open"]                # ≈ 寄り付き価格
            fb_close = fb["close"]
            fb_high  = fb["high"]
            fb_low   = fb["low"]

            # ── Step2: ギャップ計算 ──────────────────────────────────
            gap_pct = (fb_open - prev_close) / prev_close

            if gap_pct > cfg.min_gap_pct:
                direction = "long"
                # 初動確認: 第1本足が上昇（ギャップ方向と一致）
                if fb_close <= fb_open:
                    continue
                entry_price = fb_close
                initial_sl  = fb_low

            elif gap_pct < -cfg.min_gap_pct:
                direction = "short"
                if fb_close >= fb_open:
                    continue
                entry_price = fb_close
                initial_sl  = fb_high

            else:
                continue  # ギャップ不足

            # ── フィルター群 ─────────────────────────────────────────
            gap_abs = abs(fb_open - prev_close)
            # 過大ギャップ: 反転リスクが高い（例: 決算サプライズで50%ギャップ等）
            if today_atr > 0 and gap_abs > today_atr * cfg.max_gap_atr_ratio:
                logger.debug(f"[{symbol}][{trade_date.date()}] 過大ギャップ除外 "
                             f"({gap_abs:.0f} > {today_atr*cfg.max_gap_atr_ratio:.0f})")
                continue

            # SL幅チェック: 第1本足レンジが広すぎ → リスク大
            sl_width = abs(entry_price - initial_sl)
            if today_atr > 0 and sl_width > today_atr * cfg.max_sl_atr_ratio:
                logger.debug(f"[{symbol}][{trade_date.date()}] SL幅超過除外 "
                             f"({sl_width:.0f} > {today_atr*cfg.max_sl_atr_ratio:.0f})")
                continue

            if sl_width <= 0:
                continue

            # 値幅制限ゾーン
            if fb_high >= limit_high * (1 - cfg.limit_zone_pct):
                continue
            if fb_low <= limit_low * (1 + cfg.limit_zone_pct):
                continue

            # ── ポジションサイズ計算 ─────────────────────────────────
            lot_value        = cfg.lot_size * entry_price
            required_margin  = lot_value * 0.30
            if required_margin > cfg.capital:
                continue

            risk_budget      = cfg.capital * cfg.leverage * cfg.risk_per_trade_pct
            shares_by_risk   = (int(risk_budget / sl_width) // cfg.lot_size) * cfg.lot_size
            shares           = max(cfg.lot_size, shares_by_risk)
            max_buying_power = cfg.capital * cfg.leverage
            max_shares       = (int(max_buying_power / entry_price) // cfg.lot_size) * cfg.lot_size
            if max_shares > 0:
                shares = min(shares, max_shares)
            if shares <= 0:
                continue

            # ── トレードオブジェクト生成 ─────────────────────────────
            trade = GFTrade(
                symbol     = symbol,
                trade_date = trade_date.date(),
                direction  = direction,
                entry_time = first_bars.index[0],
                entry_price= entry_price,
                initial_sl = initial_sl,
                shares     = shares,
                gap_pct    = gap_pct * 100,
                atr        = today_atr,
                peak_price = entry_price,
            )

            # 第1本足以降のバーでシミュレーション
            post_df = day_df[day_df.index > first_bars.index[-1]]
            completed = self._simulate(trade, post_df, today_atr, initial_sl)
            if completed:
                trades.append(completed)

        return trades

    # ─────────────────────────────────────────────────────────────
    # トレードシミュレーション（ATRトレイリングストップ）
    # ─────────────────────────────────────────────────────────────

    def _simulate(
        self,
        trade: GFTrade,
        post_df: pd.DataFrame,
        atr: float,
        initial_sl: float,
    ) -> Optional[GFTrade]:
        """
        ATRトレイリングストップによる逐次シミュレーション

        トレイル更新ロジック:
          Long : peak を更新するたびに trail = peak - atr * factor を試みる
          Short: peak（安値更新）するたびに trail = peak + atr * factor を試みる
          → trail は有利方向にしか動かない（逆方向には動かさない）
        """
        if post_df.empty:
            return None

        cfg        = self.config
        trail_stop = initial_sl
        peak       = trade.entry_price

        for ts, row in post_df.iterrows():
            bar_time = ts.time()

            # 昼休み前強制決済
            if bar_time >= LUNCH_CLOSE and bar_time < LUNCH_START:
                trade.exit_time   = ts
                trade.exit_price  = row["open"]
                trade.exit_reason = "lunch_close"
                return trade

            # 昼休み中スキップ
            if LUNCH_START <= bar_time < LUNCH_END:
                continue

            # 引け前強制決済
            if bar_time >= FORCE_CLOSE:
                trade.exit_time   = ts
                trade.exit_price  = row["open"]
                trade.exit_reason = "force_close"
                return trade

            if trade.direction == "long":
                # ピーク更新 → トレイル引き上げ
                if row["high"] > peak:
                    peak = row["high"]
                    trade.peak_price = peak
                    new_trail = peak - atr * cfg.trail_atr_factor
                    trail_stop = max(trail_stop, new_trail)

                # トレイルSLヒット
                if row["low"] <= trail_stop:
                    trade.exit_time   = ts
                    # スリッページ: SL価格で約定（楽観的近似）
                    trade.exit_price  = trail_stop
                    trade.exit_reason = "trail_sl"
                    return trade

            else:  # short
                if row["low"] < peak:
                    peak = row["low"]
                    trade.peak_price = peak
                    new_trail = peak + atr * cfg.trail_atr_factor
                    trail_stop = min(trail_stop, new_trail)

                if row["high"] >= trail_stop:
                    trade.exit_time   = ts
                    trade.exit_price  = trail_stop
                    trade.exit_reason = "trail_sl"
                    return trade

        # 最終バーまで未決済
        if post_df.empty:
            return None
        last_bar = post_df.iloc[-1]
        trade.exit_time   = post_df.index[-1]
        trade.exit_price  = last_bar["close"]
        trade.exit_reason = "force_close"
        return trade

    # ─────────────────────────────────────────────────────────────
    # 結果集計
    # ─────────────────────────────────────────────────────────────

    def _calc_results(self) -> dict:
        trades = self.trades
        cfg    = self.config

        if not trades:
            return {"summary": {}, "trades": [], "monthly": {}}

        df = pd.DataFrame([{
            "symbol"      : t.symbol,
            "date"        : t.trade_date,
            "direction"   : t.direction,
            "entry_time"  : t.entry_time,
            "entry_price" : t.entry_price,
            "exit_price"  : t.exit_price,
            "exit_reason" : t.exit_reason,
            "pnl"         : t.pnl,
            "gap_pct"     : t.gap_pct,
            "atr"         : t.atr,
            "shares"      : t.shares,
            "hold_minutes": t.hold_minutes,
            "is_winner"   : t.is_winner,
            "mfe_pct"     : t.max_favorable_excursion_pct,
        } for t in trades])

        total        = len(df)
        wins         = int(df["is_winner"].sum())
        losses       = total - wins
        gross_profit = df.loc[df["pnl"] > 0, "pnl"].sum()
        gross_loss   = df.loc[df["pnl"] < 0, "pnl"].sum()
        total_pnl    = df["pnl"].sum()
        pf           = abs(gross_profit / gross_loss) if gross_loss != 0 else float("inf")

        cumulative  = df["pnl"].cumsum()
        max_drawdown = (cumulative - cumulative.cummax()).min()

        df["month"]  = pd.to_datetime(df["date"]).dt.to_period("M").astype(str)
        monthly      = df.groupby("month")["pnl"].sum().to_dict()

        summary = {
            "total_trades"    : total,
            "winning_trades"  : wins,
            "losing_trades"   : losses,
            "win_rate"        : wins / total * 100,
            "total_pnl"       : round(total_pnl),
            "gross_profit"    : round(gross_profit),
            "gross_loss"      : round(gross_loss),
            "profit_factor"   : round(pf, 2),
            "avg_win"         : round(df.loc[df["pnl"] > 0, "pnl"].mean()) if wins > 0 else 0,
            "avg_loss"        : round(df.loc[df["pnl"] < 0, "pnl"].mean()) if losses > 0 else 0,
            "expectancy"      : round(total_pnl / total),
            "max_drawdown"    : round(abs(max_drawdown)),
            "max_drawdown_pct": round(abs(max_drawdown) / cfg.capital * 100, 1),
            "avg_hold_minutes": round(df["hold_minutes"].mean()),
            "exit_reasons"    : df["exit_reason"].value_counts().to_dict(),
            "avg_gap_pct"     : round(df["gap_pct"].abs().mean(), 2),
            "avg_mfe_pct"     : round(df["mfe_pct"].mean(), 2),
            "long_count"      : int((df["direction"] == "long").sum()),
            "short_count"     : int((df["direction"] == "short").sum()),
        }

        return {
            "summary"     : summary,
            "trades"      : df.to_dict("records"),
            "monthly"     : monthly,
            "period_start": df["date"].min(),
            "period_end"  : df["date"].max(),
        }
