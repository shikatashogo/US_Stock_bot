"""
テクニカル分析モジュール
================================
株価履歴データからテクニカル指標を計算し、
トレンド・モメンタム・サポレジを評価する。

推奨Botでのテクニカルの役割:
  - エントリータイミングの補助（ファンダが主、テクニカルは従）
  - 損切りライン・利確ラインの精度向上（ATRベース）
  - 過熱・売られ過ぎのフィルタリング
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class TechnicalSignal:
    """テクニカル分析結果"""
    symbol: str

    # トレンド
    trend_1m: Optional[float] = None    # 1ヶ月リターン（%）
    trend_3m: Optional[float] = None    # 3ヶ月リターン（%）
    trend_6m: Optional[float] = None    # 6ヶ月リターン（%）
    trend_label: str = "不明"           # "上昇トレンド" / "下降トレンド" / "横ばい"

    # モメンタム指標
    rsi_14: Optional[float] = None      # RSI(14)
    rsi_signal: str = "中立"            # "売られ過ぎ" / "中立" / "買われ過ぎ"

    # ボラティリティ
    atr_14: Optional[float] = None      # ATR(14)（価格単位）
    atr_pct: Optional[float] = None     # ATR / 現在株価（%）

    # 出来高
    volume_ratio: Optional[float] = None  # 直近出来高 / 20日平均

    # 移動平均との位置関係
    ma20: Optional[float] = None
    ma50: Optional[float] = None
    ma200: Optional[float] = None
    above_ma20: Optional[bool] = None
    above_ma50: Optional[bool] = None
    above_ma200: Optional[bool] = None

    # サポート・レジスタンス
    support_level: Optional[float] = None    # 直近サポート水準
    resistance_level: Optional[float] = None # 直近レジスタンス水準

    # 52週高値・安値からの乖離
    pct_from_52w_high: Optional[float] = None
    pct_from_52w_low:  Optional[float] = None

    # 技術的スコア（0〜2点）
    tech_score: float = 0.0
    signals: list[str] = field(default_factory=list)


class TechnicalAnalyzer:
    """テクニカル分析クラス"""

    def analyze(self, symbol: str, price_df: pd.DataFrame) -> TechnicalSignal:
        """
        株価履歴データからテクニカル指標を計算

        Args:
            symbol   : 銘柄コード
            price_df : StockFetcher.fetch_price_history() の返り値
                       columns: open, high, low, close, volume
        Returns:
            TechnicalSignal
        """
        sig = TechnicalSignal(symbol=symbol)

        if price_df.empty or len(price_df) < 20:
            sig.signals.append("データ不足（20日未満）")
            return sig

        close  = price_df["close"]
        high   = price_df["high"] if "high" in price_df.columns else close
        low    = price_df["low"]  if "low"  in price_df.columns else close
        volume = price_df["volume"] if "volume" in price_df.columns else None

        current = float(close.iloc[-1])

        # ── トレンド ─────────────────────────────────────────────
        def pct_return(days: int) -> Optional[float]:
            if len(close) > days:
                past = float(close.iloc[-days - 1])
                return (current - past) / past * 100 if past > 0 else None
            return None

        sig.trend_1m = pct_return(21)
        sig.trend_3m = pct_return(63)
        sig.trend_6m = pct_return(126)

        t1 = sig.trend_1m or 0
        t3 = sig.trend_3m or 0
        if t1 > 3 and t3 > 5:
            sig.trend_label = "上昇トレンド"
            sig.signals.append(f"上昇トレンド継続（1M:{t1:+.1f}% 3M:{t3:+.1f}%）")
        elif t1 < -3 and t3 < -5:
            sig.trend_label = "下降トレンド"
        elif -5 <= t3 <= 5:
            sig.trend_label = "横ばい"
        else:
            sig.trend_label = "混合"

        # ── RSI ──────────────────────────────────────────────────
        rsi = self._calc_rsi(close, period=14)
        if rsi is not None:
            sig.rsi_14 = round(rsi, 1)
            if rsi < 30:
                sig.rsi_signal = "売られ過ぎ"
                sig.signals.append(f"RSI {rsi:.0f} → 売られ過ぎ水準（反発期待）")
            elif rsi < 40:
                sig.rsi_signal = "やや売られ過ぎ"
                sig.signals.append(f"RSI {rsi:.0f} → 押し目圏")
            elif rsi > 75:
                sig.rsi_signal = "買われ過ぎ"
                sig.signals.append(f"RSI {rsi:.0f} → 買われ過ぎ（過熱注意）")
            elif rsi > 60:
                sig.rsi_signal = "やや買われ過ぎ"
            else:
                sig.rsi_signal = "中立"

        # ── ATR ──────────────────────────────────────────────────
        atr = self._calc_atr(high, low, close, period=14)
        if atr is not None:
            sig.atr_14 = round(atr, 2)
            sig.atr_pct = round(atr / current * 100, 2) if current > 0 else None

        # ── 移動平均 ──────────────────────────────────────────────
        for period, attr in [(20, "ma20"), (50, "ma50"), (200, "ma200")]:
            if len(close) >= period:
                ma = float(close.tail(period).mean())
                setattr(sig, attr, round(ma, 2))
                above_attr = f"above_{attr}"
                setattr(sig, above_attr, current > ma)

        if sig.above_ma200 and sig.above_ma50 and sig.above_ma20:
            sig.signals.append("全移動平均の上方（強気アライメント）")
        elif sig.above_ma200 is False and sig.above_ma50 is False:
            sig.signals.append("主要移動平均の下方（弱気）")

        # ゴールデンクロス・デッドクロス確認（直近10日）
        if sig.ma20 and sig.ma50:
            if sig.above_ma20 and sig.ma20 > sig.ma50:
                # MA20がMA50を上回っている = ゴールデン傾向
                sig.signals.append("短期MA(20)が中期MA(50)を上回る")

        # ── 出来高 ────────────────────────────────────────────────
        if volume is not None and len(volume) >= 20:
            avg_vol = float(volume.tail(20).mean())
            recent_vol = float(volume.iloc[-1])
            if avg_vol > 0:
                sig.volume_ratio = round(recent_vol / avg_vol, 2)
                if sig.volume_ratio > 2.0:
                    sig.signals.append(f"出来高急増（平均比 {sig.volume_ratio:.1f}倍）")

        # ── サポート・レジスタンス ─────────────────────────────────
        if len(close) >= 20:
            recent_20 = close.tail(20)
            sig.support_level    = round(float(recent_20.min()), 2)
            sig.resistance_level = round(float(recent_20.max()), 2)

        # ── 52週高値・安値からの乖離 ──────────────────────────────
        if len(close) >= 252:
            yearly = close.tail(252)
        else:
            yearly = close
        w52_high = float(yearly.max())
        w52_low  = float(yearly.min())
        if w52_high > 0:
            sig.pct_from_52w_high = round((current - w52_high) / w52_high * 100, 1)
        if w52_low > 0 and w52_low < current:
            sig.pct_from_52w_low = round((current - w52_low) / w52_low * 100, 1)

        # ── テクニカルスコア ──────────────────────────────────────
        sig.tech_score = self._calc_tech_score(sig)
        return sig

    # ─── 指標計算 ────────────────────────────────────────────────

    @staticmethod
    def _calc_rsi(close: pd.Series, period: int = 14) -> Optional[float]:
        """RSI計算"""
        if len(close) < period + 1:
            return None
        delta = close.diff().dropna()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1 + rs))

    @staticmethod
    def _calc_atr(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
    ) -> Optional[float]:
        """ATR計算（EWM方式）"""
        if len(close) < period + 1:
            return None
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.ewm(span=period, adjust=False).mean()
        return float(atr.iloc[-1])

    @staticmethod
    def _calc_tech_score(sig: TechnicalSignal) -> float:
        """テクニカル指標からスコア計算（0〜2点）"""
        score = 1.0  # 中立基準点

        # トレンド
        t1 = sig.trend_1m or 0
        if t1 > 5:    score += 0.4
        elif t1 > 2:  score += 0.2
        elif t1 < -5: score -= 0.4
        elif t1 < -2: score -= 0.2

        # RSI（売られ過ぎ = 買いやすい）
        rsi = sig.rsi_14
        if rsi is not None:
            if rsi < 35:   score += 0.4
            elif rsi < 45: score += 0.2
            elif rsi > 75: score -= 0.3

        # 移動平均アライメント
        if sig.above_ma200 and sig.above_ma50:
            score += 0.3
        elif sig.above_ma200 is False and sig.above_ma50 is False:
            score -= 0.3

        # 52週安値からの距離（低い = 割安な可能性）
        pct_from_low = sig.pct_from_52w_low
        if pct_from_low is not None:
            if pct_from_low < 20:   score += 0.3   # 52週安値から20%以内
            elif pct_from_low > 80: score -= 0.2   # 52週高値圏

        return round(max(0.0, min(2.0, score)), 2)


def batch_analyze_technical(
    price_data: dict[str, pd.DataFrame]
) -> dict[str, TechnicalSignal]:
    """複数銘柄のテクニカル分析を一括実行"""
    analyzer = TechnicalAnalyzer()
    return {symbol: analyzer.analyze(symbol, df) for symbol, df in price_data.items()}
