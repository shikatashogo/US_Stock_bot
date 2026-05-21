"""
日次レポート生成モジュール
1日の取引結果を分析して、反省点と翌日の注目銘柄をまとめる
Claude APIを使用してAI分析レポートを生成する
"""
from typing import Dict, List, Optional
from datetime import datetime, date
import os
import pytz
from loguru import logger

try:
    import anthropic
    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False

from ..execution.trade_logger import TradeLogger
from ..data.market_data import MarketDataFetcher
from ..data.universe import UniverseManager


class DailyReportGenerator:
    """日次レポートを生成するクラス"""

    def __init__(
        self,
        trade_logger: TradeLogger,
        data: MarketDataFetcher,
        universe: UniverseManager,
        settings: dict,
    ):
        self.trade_logger = trade_logger
        self.data = data
        self.universe = universe
        self.settings = settings
        self.et_tz = pytz.timezone("America/New_York")
        self.jst_tz = pytz.timezone("Asia/Tokyo")

        # Claude API クライアント
        self.claude = None
        if CLAUDE_AVAILABLE and os.getenv("ANTHROPIC_API_KEY"):
            self.claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    def generate(self, portfolio_value: float, portfolio_pnl: float) -> Dict:
        """日次レポートを生成して返す"""
        # ET（米国東部時間）の日付を使用（取引ログはETで保存されているため）
        now_et = datetime.now(self.et_tz)
        today = now_et.date().isoformat()
        today_trades = self.trade_logger.get_trades_for_date(today)
        perf_stats = self.trade_logger.get_performance_stats(days=30)

        closed_trades = [t for t in today_trades if t.get("status") == "closed"]
        winning = [t for t in closed_trades if t.get("pnl", 0) > 0]
        losing = [t for t in closed_trades if t.get("pnl", 0) < 0]

        # 取引損益（DBの実際の取引ベース）← 為替変動を含まない正確な数値
        trade_pnl = sum(t.get("pnl", 0) for t in closed_trades)
        trade_pnl_pct = round(trade_pnl / portfolio_value * 100, 2) if portfolio_value > 0 else 0

        best_trade = max(closed_trades, key=lambda t: t.get("pnl", 0), default=None) if closed_trades else None
        worst_trade = min(closed_trades, key=lambda t: t.get("pnl", 0), default=None) if closed_trades else None

        # 当月・当年累計損益
        monthly_pnl = self._get_period_pnl("month")
        yearly_pnl = self._get_period_pnl("year")

        # 明日の注目銘柄
        tomorrow_watch = self._get_tomorrow_watchlist()

        # AI分析による反省点
        reflections = self._generate_reflections(
            today_trades, trade_pnl, portfolio_value, perf_stats
        )

        report = {
            "date": today,
            "portfolio_value": portfolio_value,
            "daily_pnl": trade_pnl,           # 取引損益（正確）
            "daily_pnl_pct": trade_pnl_pct,
            "portfolio_pnl": portfolio_pnl,   # ポートフォリオ増減（為替含む）
            "monthly_pnl": monthly_pnl,
            "yearly_pnl": yearly_pnl,
            "trades_count": len(closed_trades),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": round(len(winning) / len(closed_trades) * 100) if closed_trades else 0,
            "best_trade": {"symbol": best_trade["symbol"], "pnl": best_trade["pnl"]} if best_trade and best_trade.get("pnl", 0) > 0 else None,
            "worst_trade": {"symbol": worst_trade["symbol"], "pnl": worst_trade["pnl"]} if worst_trade and worst_trade.get("pnl", 0) < 0 else None,
            "30day_stats": perf_stats,
            "reflections": reflections,
            "tomorrow_watch": tomorrow_watch,
            "trades": closed_trades,
        }

        # 日次統計をDBに保存
        self.trade_logger.save_daily_stats(portfolio_value, trade_pnl)

        logger.info(f"日次レポート生成完了: {today}")
        return report

    def _get_period_pnl(self, period: str) -> float:
        """当月または当年の累計取引損益をDBから取得する"""
        import sqlite3
        from pathlib import Path
        db_path = Path("data/trades.db")
        now_et = datetime.now(self.et_tz)

        if period == "month":
            since = now_et.strftime("%Y-%m-01")
        else:  # year
            since = now_et.strftime("%Y-01-01")

        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE status='closed' AND DATE(entry_time) >= ?",
                    (since,)
                ).fetchone()
                return round(row[0], 2) if row else 0.0
        except Exception:
            return 0.0

    def _get_tomorrow_watchlist(self) -> List[str]:
        """
        翌日の注目銘柄リストを生成する
        テスタ哲学: 毎日市場を観察し、準備する
        """
        momentum_stocks = self.universe.screen_momentum_stocks(min_momentum_pct=2.0)
        breakout_candidates = self.universe.screen_breakout_candidates()

        watchlist = []

        # モメンタム上位3銘柄
        for stock in momentum_stocks[:3]:
            watchlist.append(
                f"{stock['symbol']} (モメンタム +{stock['momentum_5d_pct']}%, "
                f"出来高{stock['volume_ratio']}x)"
            )

        # ブレイクアウト候補
        for stock in breakout_candidates[:2]:
            symbol = stock["symbol"]
            if symbol not in [w.split()[0] for w in watchlist]:
                watchlist.append(
                    f"{stock['symbol']} (ブレイクアウト候補, "
                    f"抵抗線まで{stock['pct_from_high']:.1f}%)"
                )

        return watchlist[:5]

    def _generate_reflections(
        self,
        trades: List[Dict],
        daily_pnl: float,
        portfolio_value: float,
        perf_stats: Dict,
    ) -> List[str]:
        """
        取引の反省点を生成する
        Claude APIが利用可能な場合はAI分析を使用
        """
        if self.claude and trades:
            return self._ai_reflections(trades, daily_pnl, portfolio_value, perf_stats)
        else:
            return self._rule_based_reflections(trades, daily_pnl, perf_stats)

    def _ai_reflections(
        self,
        trades: List[Dict],
        daily_pnl: float,
        portfolio_value: float,
        perf_stats: Dict,
    ) -> List[str]:
        """Claude APIを使ってAI分析による反省点を生成する"""
        try:
            trades_summary = "\n".join([
                f"- {t['symbol']}: {t.get('strategy','')}, "
                f"損益${t.get('pnl',0):.2f}, "
                f"理由: {t.get('signal_reason','N/A')[:80]}"
                for t in trades if t.get("status") == "closed"
            ])

            prompt = f"""あなたは米国株のベテラントレーダーです。
テスタ氏の「負けないことを最優先」の投資哲学に基づいて、今日の取引を振り返り、
改善点を3〜5点、箇条書きで日本語で教えてください。

【本日の取引データ】
日次損益: ${daily_pnl:.2f}
ポートフォリオ: ${portfolio_value:.2f}
勝率(30日): {perf_stats.get('win_rate', 0):.0f}%

取引詳細:
{trades_summary if trades_summary else "本日の取引なし"}

以下の観点で分析してください:
1. エントリーのタイミングは適切だったか
2. 損切りは適切に実行されたか
3. 市場全体のトレンドを考慮できていたか
4. 出来高確認は適切だったか
5. ポジションサイズは適切だったか

各反省点は1〜2文で簡潔に。箇条書きなし（改行区切り）。"""

            model = self.settings.get("ai_optimizer", {}).get("model", "claude-sonnet-4-6")
            response = self.claude.messages.create(
                model=model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.content[0].text.strip()
            reflections = [r.strip() for r in content.split("\n") if r.strip()][:5]
            return reflections

        except Exception as e:
            logger.error(f"AI反省点生成エラー: {e}")
            return self._rule_based_reflections(trades, daily_pnl, perf_stats)

    def _rule_based_reflections(
        self,
        trades: List[Dict],
        daily_pnl: float,
        perf_stats: Dict,
    ) -> List[str]:
        """ルールベースの反省点生成（Claude APIが使えない場合のフォールバック）"""
        reflections = []
        closed_trades = [t for t in trades if t.get("status") == "closed"]

        if not closed_trades:
            return ["本日は取引なし。市場を観察し、明日のセットアップを準備しました。"]

        # 損切り取引の分析
        stop_losses = [t for t in closed_trades if t.get("exit_reason") == "stop_loss"]
        if stop_losses:
            reflections.append(
                f"損切り{len(stop_losses)}件実行。損切りを迷わず実行できた点は評価できます。"
                f"エントリーの選定をより厳格にすることで損切り件数を減らせる可能性があります。"
            )

        # 勝率の分析
        winners = [t for t in closed_trades if t.get("pnl", 0) > 0]
        win_rate = len(winners) / len(closed_trades) * 100
        if win_rate < 50:
            reflections.append(
                f"本日勝率{win_rate:.0f}%。テスタ基準ではRR比を高めることで"
                f"勝率が低くても利益を出せますが、エントリー基準の見直しを検討してください。"
            )
        elif win_rate >= 60:
            reflections.append(f"本日勝率{win_rate:.0f}%。良好なエントリー選定ができています。")

        # 全体損益の分析
        if daily_pnl < 0:
            reflections.append(
                f"本日は損失（${daily_pnl:.2f}）。"
                f"損失は取引の一部です。ルールを守れたかが重要です。"
            )
        else:
            reflections.append(
                f"本日は利益（${daily_pnl:.2f}）。戦略を継続してください。"
            )

        return reflections
