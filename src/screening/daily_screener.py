"""
日次スクリーニングモジュール
==============================
翌日のORBトレード候補銘柄を毎日夜間に選定する

スクリーニングの設計思想:
  「その日だけ特別に動きやすい銘柄を選ぶ」

  固定ユニバース全銘柄にORBを打つのは精度が低い。
  cis氏の手法 → 「勢いのある銘柄のみ」を追う
  ラリーウィリアムズのNR7 → 「圧縮レンジ翌日の爆発」を狙う
  機関投資家の代理指標 → 「出来高異常」を使う

スコアリング因子（全て無料データで計算可能）:
  1. NR7パターン     : 直近7日最小レンジ → 翌日ブレイク確率高い (+3点)
  2. 出来高異常      : 前日出来高が20日平均の1.5倍超 → 機関参入シグナル (+3点)
  3. 高値/安値引け   : 前日終値が当日レンジの上下25%以内 → 方向バイアス (+2点)
  4. ATR活性度      : 値幅ポテンシャルの確認 (+1.5点)
  5. 5日モメンタム   : トレンドの強さ確認 (+1点)
  6. 市場環境       : 日経・ドル円の方向性 (+1点)

実行タイミング: 毎日16:00〜翌朝8:00の間（取引終了後）
データソース: yfinance日足（完全無料）
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import pytz
import yfinance as yf
from loguru import logger

from .universe_manager import UniverseManager

JST = pytz.timezone("Asia/Tokyo")


# ─── データクラス ─────────────────────────────────────────────────

@dataclass
class MarketContext:
    """当日の市場環境スナップショット"""
    date: str
    nikkei_close: float
    nikkei_change_pct: float          # 前日比%
    nikkei_5d_trend: str              # "up" / "down" / "flat"
    usdjpy: float
    usdjpy_change_pct: float
    vix: float
    market_regime: str                # "offensive" / "neutral" / "defensive"
    notes: List[str] = field(default_factory=list)


@dataclass
class CandidateScore:
    """銘柄スコアリング結果"""
    code: str
    name: str
    score: float
    signals: List[str]               # 発動したシグナルの説明
    direction_bias: str              # "long" / "short" / "neutral"

    # スクリーニング用参照値
    prev_close: float
    prev_volume: float
    vol_ratio: float                 # 前日出来高 / 20日平均
    atr_pct: float                   # ATR / 前日終値 (%)
    is_nr7: bool
    close_position: float            # 前日終値のレンジ内位置 (0=安値 1=高値)


@dataclass
class ScreeningResult:
    """スクリーニング実行結果（JSONで保存）"""
    screening_date: str              # スクリーニング実施日
    target_date: str                 # この候補を使う取引日
    market_context: dict
    candidates: List[dict]           # CandidateScore のリスト（上位N件）
    all_scores: List[dict]           # 全銘柄スコア（デバッグ用）
    notes: List[str] = field(default_factory=list)


# ─── メインクラス ─────────────────────────────────────────────────

class DailyScreener:
    """
    翌日のORBトレード候補を選定するスクリーナー

    使い方:
        screener = DailyScreener()
        result = screener.run()
        # result.candidates → 翌日に狙う銘柄リスト（スコア降順）
    """

    def __init__(
        self,
        capital: float = 500_000,
        top_n: int = 8,
        min_score: float = 3.0,
        output_dir: Optional[str] = None,
    ):
        self.capital = capital
        self.top_n = top_n
        self.min_score = min_score
        self.universe_mgr = UniverseManager(capital=capital)

        if output_dir is None:
            base = Path(__file__).resolve().parents[2]
            output_dir = str(base / "data" / "screening")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ─────────────────────────────────────────────────────────────
    # 公開メソッド
    # ─────────────────────────────────────────────────────────────

    def run(self, save: bool = True) -> ScreeningResult:
        """スクリーニングを実行して結果を返す"""
        today = datetime.now(JST)
        logger.info(f"=== 日次スクリーニング開始 ({today.strftime('%Y-%m-%d %H:%M')}) ===")

        # 1. 取引可能ユニバース取得
        universe = self.universe_mgr.get_tradeable_universe(verbose=True)
        if not universe:
            logger.error("取引可能銘柄が取得できませんでした")
            return self._empty_result(today)

        # 2. 日足データ取得（3ヶ月分 = ATR・出来高平均計算に十分）
        daily_data = self.universe_mgr.fetch_daily_data(
            list(universe.keys()), period="3mo", use_cache=True
        )

        # 3. 市場環境取得
        ctx = self._get_market_context(today)
        logger.info(
            f"市場環境: 日経{ctx.nikkei_change_pct:+.2f}% / "
            f"ドル円{ctx.usdjpy:.1f} / VIX{ctx.vix:.1f} → {ctx.market_regime}"
        )

        # 4. 全銘柄スコアリング
        all_scores: List[CandidateScore] = []
        for code, name in universe.items():
            if code not in daily_data:
                continue
            score_obj = self._score_candidate(code, name, daily_data[code], ctx)
            if score_obj:
                all_scores.append(score_obj)

        # スコア降順にソート
        all_scores.sort(key=lambda x: x.score, reverse=True)

        # 5. 最低スコアフィルターを適用して上位N件を選出
        candidates = [s for s in all_scores if s.score >= self.min_score][: self.top_n]

        logger.info(
            f"スクリーニング完了: {len(candidates)}銘柄を選出 "
            f"(スコア閾値{self.min_score}以上)"
        )
        for c in candidates:
            logger.info(
                f"  [{c.code}] {c.name:<10} スコア{c.score:.1f} "
                f"| {', '.join(c.signals)}"
            )

        # 6. 結果を構築・保存
        result = ScreeningResult(
            screening_date=today.strftime("%Y-%m-%d"),
            target_date=self._next_trading_day(today),
            market_context=asdict(ctx),
            candidates=[asdict(c) for c in candidates],
            all_scores=[asdict(s) for s in all_scores],
            notes=self._generate_notes(ctx, candidates),
        )

        if save:
            self._save_result(result, today)

        return result

    def load_latest(self) -> Optional[dict]:
        """最新のスクリーニング結果をJSONから読み込む"""
        files = sorted(self.output_dir.glob("screening_*.json"), reverse=True)
        if not files:
            logger.warning("スクリーニング結果ファイルが見つかりません")
            return None
        with open(files[0]) as f:
            return json.load(f)

    def print_result(self, result: ScreeningResult) -> None:
        """スクリーニング結果を見やすく表示"""
        ctx = result.market_context
        print("\n" + "=" * 60)
        print(f"  日次スクリーニング結果 [{result.target_date} 取引用]")
        print("=" * 60)
        print(f"  市場環境: {ctx['market_regime'].upper()}")
        print(
            f"  日経{ctx['nikkei_change_pct']:+.2f}% / "
            f"ドル円{ctx['usdjpy']:.1f} / VIX{ctx['vix']:.1f}"
        )
        print("-" * 60)
        print(f"  {'順位':>4} {'コード':>6} {'銘柄':>10} {'スコア':>6} "
              f"{'Vol比':>6} {'ATR%':>5} {'NR7':>4} {'バイアス':>8}")
        print("-" * 60)
        for i, c in enumerate(result.candidates, 1):
            nr7_mark = "✓" if c["is_nr7"] else "-"
            print(
                f"  {i:>4}位 {c['code']:>6} {c['name']:>10} "
                f"{c['score']:>6.1f} {c['vol_ratio']:>6.1f}x "
                f"{c['atr_pct']*100:>5.1f}% {nr7_mark:>4} "
                f"{c['direction_bias']:>8}"
            )
            print(f"        シグナル: {', '.join(c['signals'])}")
        print("=" * 60)
        if result.notes:
            print("  注意事項:")
            for note in result.notes:
                print(f"    ⚠ {note}")
            print()

    # ─────────────────────────────────────────────────────────────
    # スコアリングロジック（シグナル定義）
    # ─────────────────────────────────────────────────────────────

    def _score_candidate(
        self,
        code: str,
        name: str,
        daily_df: pd.DataFrame,
        ctx: MarketContext,
    ) -> Optional[CandidateScore]:
        """
        1銘柄のスコアを計算する

        スコア満点 ≈ 11.5点
        推奨閾値: 3.0点以上 → 「何かが起きている銘柄」
        """
        if len(daily_df) < 20:
            return None

        score = 0.0
        signals = []

        prev = daily_df.iloc[-1]   # 前日データ
        prev_close = prev["close"]
        prev_high = prev["high"]
        prev_low = prev["low"]
        prev_vol = prev["volume"]

        # ── シグナル①: NR7（ラリーウィリアムズ手法）────────────────
        # 「前日が直近7日間で最も小さいレンジ」→ エネルギー蓄積 → 翌日爆発的動き
        # 参照: "How to Trade Stocks and Commodities" - Larry Williams
        ranges = daily_df["high"] - daily_df["low"]
        prev_range = ranges.iloc[-1]
        nr7_min = ranges.iloc[-7:].min()
        is_nr7 = bool(prev_range <= nr7_min)  # numpy.bool_ → Python bool (JSON対応)

        if is_nr7:
            score += 3.0
            signals.append("NR7★レンジ圧縮")
        elif prev_range <= ranges.iloc[-7:].quantile(0.30):
            score += 1.5
            signals.append("準NR7(狭レンジ)")

        # ── シグナル②: 出来高異常（機関投資家参入の代理指標）──────────
        # 通常の1.5倍超 → 大口が動いている = 方向性が出やすい
        avg_vol_20d = daily_df["volume"].iloc[-20:].mean()
        vol_ratio = prev_vol / avg_vol_20d if avg_vol_20d > 0 else 1.0

        if vol_ratio >= 2.5:
            score += 3.0
            signals.append(f"出来高急増{vol_ratio:.1f}x★")
        elif vol_ratio >= 1.8:
            score += 2.0
            signals.append(f"出来高増{vol_ratio:.1f}x")
        elif vol_ratio >= 1.3:
            score += 1.0
            signals.append(f"出来高やや増{vol_ratio:.1f}x")
        elif vol_ratio < 0.7:
            score -= 1.0
            signals.append(f"出来高低({vol_ratio:.1f}x)")

        # ── シグナル③: 高値/安値引け（翌日の方向バイアス）─────────────
        # 前日終値がレンジ上位25% → ロングバイアス
        # 前日終値がレンジ下位25% → ショートバイアス
        close_position = 0.5
        if prev_high > prev_low:
            close_position = (prev_close - prev_low) / (prev_high - prev_low)
        direction_bias = "neutral"

        if close_position >= 0.75:
            score += 1.5
            signals.append(f"高値引け({close_position:.0%})")
            direction_bias = "long"
        elif close_position <= 0.25:
            score += 1.5
            signals.append(f"安値引け({close_position:.0%})")
            direction_bias = "short"

        # ── シグナル④: ATR活性度（値幅ポテンシャル）──────────────────
        # ATRが前日終値の1.5%以上 → 1日で十分な利幅が取れる
        tr = pd.concat([
            daily_df["high"] - daily_df["low"],
            (daily_df["high"] - daily_df["close"].shift(1)).abs(),
            (daily_df["low"] - daily_df["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr14 = tr.ewm(span=14, adjust=False).mean().iloc[-1]
        atr_pct = atr14 / prev_close if prev_close > 0 else 0

        if atr_pct >= 0.025:
            score += 1.5
            signals.append(f"ATR高{atr_pct*100:.1f}%")
        elif atr_pct >= 0.015:
            score += 0.5
            signals.append(f"ATR中{atr_pct*100:.1f}%")
        elif atr_pct < 0.008:
            score -= 1.0
            signals.append(f"ATR低{atr_pct*100:.1f}%(-)")

        # ── シグナル⑤: 5日モメンタム（トレンドの強さ）──────────────
        # 5営業日で3%以上動いている → 勢いがある
        if len(daily_df) >= 6:
            close_5d_ago = daily_df["close"].iloc[-6]
            momentum_5d = (prev_close - close_5d_ago) / close_5d_ago
            if abs(momentum_5d) >= 0.04:
                score += 1.0
                direction_str = "上昇" if momentum_5d > 0 else "下落"
                signals.append(f"5日モメンタム{direction_str}{abs(momentum_5d)*100:.1f}%")
                if momentum_5d > 0 and direction_bias == "neutral":
                    direction_bias = "long"
                elif momentum_5d < 0 and direction_bias == "neutral":
                    direction_bias = "short"

        # ── シグナル⑥: 市場環境との整合性 ──────────────────────────
        # 「市場が荒れている日」はORBが機能しやすい
        if abs(ctx.nikkei_change_pct) >= 0.5:
            score += 0.5
            direction_str = "上昇" if ctx.nikkei_change_pct > 0 else "下落"
            signals.append(f"日経{direction_str}環境")

        # defensive環境ではスコアにペナルティ
        if ctx.market_regime == "defensive":
            score *= 0.8
            signals.append("防衛モード調整")

        return CandidateScore(
            code=code,
            name=name,
            score=float(round(score, 1)),
            signals=signals,
            direction_bias=direction_bias,
            prev_close=float(round(prev_close, 1)),
            prev_volume=int(prev_vol),
            vol_ratio=float(round(vol_ratio, 2)),
            atr_pct=float(round(atr_pct, 4)),
            is_nr7=is_nr7,
            close_position=float(round(close_position, 2)),
        )

    # ─────────────────────────────────────────────────────────────
    # 市場環境の取得
    # ─────────────────────────────────────────────────────────────

    def _get_market_context(self, as_of: datetime) -> MarketContext:
        """
        日経225・ドル円・VIXから市場環境を判定する
        全て yfinance で取得（無料）
        """
        nikkei_close = 0.0
        nikkei_change_pct = 0.0
        nikkei_5d_trend = "flat"
        usdjpy = 150.0
        usdjpy_change_pct = 0.0
        vix = 20.0
        notes = []

        try:
            # 日経225
            nk = yf.Ticker("^N225")
            nk_hist = nk.history(period="10d", interval="1d")
            if len(nk_hist) >= 2:
                nikkei_close = nk_hist["Close"].iloc[-1]
                prev = nk_hist["Close"].iloc[-2]
                nikkei_change_pct = (nikkei_close - prev) / prev * 100
                sma5 = nk_hist["Close"].iloc[-5:].mean()
                nikkei_5d_trend = (
                    "up" if nikkei_close > sma5 * 1.005
                    else "down" if nikkei_close < sma5 * 0.995
                    else "flat"
                )
        except Exception as e:
            logger.warning(f"日経データ取得失敗: {e}")
            notes.append("日経データ取得失敗（市場環境判定に影響）")

        try:
            # ドル円
            jpy = yf.Ticker("JPY=X")
            jpy_hist = jpy.history(period="5d", interval="1d")
            if len(jpy_hist) >= 2:
                usdjpy = jpy_hist["Close"].iloc[-1]
                prev_jpy = jpy_hist["Close"].iloc[-2]
                usdjpy_change_pct = (usdjpy - prev_jpy) / prev_jpy * 100
        except Exception as e:
            logger.warning(f"ドル円データ取得失敗: {e}")
            notes.append("ドル円データ取得失敗")

        try:
            # VIX（米国恐怖指数を参考値として使用）
            vix_ticker = yf.Ticker("^VIX")
            vix_hist = vix_ticker.history(period="3d", interval="1d")
            if len(vix_hist) >= 1:
                vix = vix_hist["Close"].iloc[-1]
        except Exception as e:
            logger.warning(f"VIXデータ取得失敗: {e}")

        # 市場環境判定ロジック
        # offensive: 積極的にトレード
        # neutral:   通常通りトレード
        # defensive: ポジションサイズを半分にする
        if vix > 30 or abs(nikkei_change_pct) > 3.0:
            regime = "defensive"
            notes.append(f"VIX高水準({vix:.1f}) or 日経大変動({nikkei_change_pct:+.1f}%)")
        elif vix < 15 and abs(nikkei_change_pct) < 0.5:
            regime = "neutral"  # 低ボラティリティ → ORBが機能しにくい
            notes.append("低ボラティリティ環境: ORBのレンジが小さくなりがち")
        elif abs(nikkei_change_pct) >= 1.0:
            regime = "offensive"  # 方向感がある日 → ORBが機能しやすい
        else:
            regime = "neutral"

        return MarketContext(
            date=as_of.strftime("%Y-%m-%d"),
            nikkei_close=round(nikkei_close, 0),
            nikkei_change_pct=round(nikkei_change_pct, 2),
            nikkei_5d_trend=nikkei_5d_trend,
            usdjpy=round(usdjpy, 2),
            usdjpy_change_pct=round(usdjpy_change_pct, 2),
            vix=round(vix, 1),
            market_regime=regime,
            notes=notes,
        )

    # ─────────────────────────────────────────────────────────────
    # ユーティリティ
    # ─────────────────────────────────────────────────────────────

    def _generate_notes(
        self, ctx: MarketContext, candidates: List[CandidateScore]
    ) -> List[str]:
        """運用上の注意事項を自動生成"""
        notes = list(ctx.notes)

        if ctx.market_regime == "defensive":
            notes.append("本日は防衛モード: ポジションサイズを通常の50%に縮小推奨")

        nr7_count = sum(1 for c in candidates if c.is_nr7)
        if nr7_count >= 3:
            notes.append(f"NR7銘柄が{nr7_count}件: 市場全体でレンジが圧縮 → 明日は大きな動きに備える")

        high_vol_count = sum(1 for c in candidates if c.vol_ratio >= 2.0)
        if high_vol_count >= 3:
            notes.append(f"出来高急増銘柄が{high_vol_count}件: 市場参加者の関心が高まっている")

        return notes

    def _next_trading_day(self, today: datetime) -> str:
        """翌営業日の日付を返す（簡易版: 土日を除くのみ）"""
        from datetime import timedelta
        next_day = today + timedelta(days=1)
        while next_day.weekday() >= 5:  # 土=5, 日=6
            next_day += timedelta(days=1)
        return next_day.strftime("%Y-%m-%d")

    def _save_result(self, result: ScreeningResult, today: datetime) -> None:
        """結果をJSONファイルに保存"""
        filename = f"screening_{today.strftime('%Y%m%d')}.json"
        path = self.output_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(result), f, ensure_ascii=False, indent=2)
        logger.info(f"スクリーニング結果を保存: {path}")

    # ─────────────────────────────────────────────────────────────
    # バックテスト統合用メソッド（ルックアヘッドバイアスなし）
    # ─────────────────────────────────────────────────────────────

    def score_candidates_for_date(
        self,
        all_daily_data: Dict[str, pd.DataFrame],
        universe: Dict[str, str],
        cutoff_date: pd.Timestamp,
        nikkei_hist: pd.DataFrame,
        usdjpy_hist: pd.DataFrame,
        vix_hist: pd.DataFrame,
    ) -> List[CandidateScore]:
        """
        バックテスト用スクリーニング（ルックアヘッドバイアスなし）

        cutoff_date の前日までのデータのみを使い、その日の候補をスコアリングする。
        例: cutoff_date = 2026-03-01 → 2026-02-28 以前のデータのみ使用

        Args:
            all_daily_data: 全銘柄の日足DataFrame（index は timezone-aware）
            universe:       取引可能銘柄の {code: name} dict
            cutoff_date:    取引日（この日の朝時点で利用可能なデータに限定）
            nikkei_hist:    日経225日足履歴
            usdjpy_hist:    ドル円日足履歴
            vix_hist:       VIX日足履歴
        """
        # cutoff_date 以前のデータのみ使用（look-ahead bias 防止）
        cutoff_naive = cutoff_date.normalize()

        ctx = self._get_historical_market_context(
            cutoff_date, nikkei_hist, usdjpy_hist, vix_hist
        )

        scores: List[CandidateScore] = []
        for code, name in universe.items():
            if code not in all_daily_data:
                continue
            df_full = all_daily_data[code]
            # cutoff_date の前日までに限定
            df_sliced = df_full[df_full.index.normalize() < cutoff_naive]
            if len(df_sliced) < 20:
                continue
            score_obj = self._score_candidate(code, name, df_sliced, ctx)
            if score_obj:
                scores.append(score_obj)

        scores.sort(key=lambda x: x.score, reverse=True)
        return scores

    def _get_historical_market_context(
        self,
        cutoff_date: pd.Timestamp,
        nikkei_hist: pd.DataFrame,
        usdjpy_hist: pd.DataFrame,
        vix_hist: pd.DataFrame,
    ) -> MarketContext:
        """
        バックテスト用: 履歴データから特定取引日の「前日市場環境」を復元する
        cutoff_date の直前の営業日データを参照
        """
        cutoff_naive = cutoff_date.normalize()

        def _get_prev(hist: pd.DataFrame, default: float) -> tuple:
            """カットオフ前の最新2行を返す"""
            past = hist[hist.index.normalize() < cutoff_naive]
            if len(past) < 2:
                return default, 0.0
            c = float(past["Close"].iloc[-1])
            p = float(past["Close"].iloc[-2])
            return c, (c - p) / p * 100 if p > 0 else 0.0

        nk_close, nk_chg = _get_prev(nikkei_hist, 38000.0)
        usdjpy, jpy_chg = _get_prev(usdjpy_hist, 150.0)
        vix_val, _ = _get_prev(vix_hist, 20.0)

        # 5日トレンド
        past_nk = nikkei_hist[nikkei_hist.index.normalize() < cutoff_naive]
        sma5 = float(past_nk["Close"].iloc[-5:].mean()) if len(past_nk) >= 5 else nk_close
        nk_trend = (
            "up" if nk_close > sma5 * 1.005
            else "down" if nk_close < sma5 * 0.995
            else "flat"
        )

        if vix_val > 30 or abs(nk_chg) > 3.0:
            regime = "defensive"
        elif abs(nk_chg) >= 1.0:
            regime = "offensive"
        else:
            regime = "neutral"

        return MarketContext(
            date=cutoff_date.strftime("%Y-%m-%d"),
            nikkei_close=round(nk_close, 0),
            nikkei_change_pct=round(nk_chg, 2),
            nikkei_5d_trend=nk_trend,
            usdjpy=round(usdjpy, 2),
            usdjpy_change_pct=round(jpy_chg, 2),
            vix=round(vix_val, 1),
            market_regime=regime,
        )

    def _empty_result(self, today: datetime) -> ScreeningResult:
        return ScreeningResult(
            screening_date=today.strftime("%Y-%m-%d"),
            target_date=self._next_trading_day(today),
            market_context={},
            candidates=[],
            all_scores=[],
            notes=["スクリーニング失敗: データ取得エラー"],
        )
