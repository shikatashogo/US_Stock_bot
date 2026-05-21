"""
バックテスト指標計算モジュール
シャープレシオ・最大ドローダウン・プロフィットファクター等を計算する
"""
from typing import List, Dict
import math
import pandas as pd
import numpy as np
from loguru import logger


class BacktestMetrics:
    """バックテスト結果の指標を計算するクラス"""

    def calculate(
        self,
        trades: List[Dict],
        portfolio_history: List[Dict],
        initial_capital: float,
        symbol: str = "",
    ) -> Dict:
        """全指標を計算して結果を返す"""
        if not trades:
            return {
                "symbol": symbol,
                "total_trades": 0,
                "message": "バックテスト期間中に取引なし",
            }

        pnls = [t["pnl"] for t in trades]
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        win_pnls = [t["pnl"] for t in wins]
        loss_pnls = [t["pnl"] for t in losses]

        total_pnl = sum(pnls)
        final_capital = initial_capital + total_pnl
        total_return_pct = (total_pnl / initial_capital) * 100

        win_rate = len(wins) / len(trades) * 100 if trades else 0
        avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0
        avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0
        profit_factor = abs(sum(win_pnls)) / abs(sum(loss_pnls)) if loss_pnls else float("inf")
        max_drawdown, max_drawdown_pct = self._calculate_max_drawdown(portfolio_history, initial_capital)
        sharpe = self._calculate_sharpe_ratio(pnls, initial_capital)
        expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

        min_trades = 30
        is_statistically_valid = len(trades) >= min_trades

        exit_reasons = {}
        for t in trades:
            reason = t.get("exit_reason", "unknown")
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

        strategy_breakdown = {}
        for t in trades:
            strat = t.get("strategy", "unknown")
            if strat not in strategy_breakdown:
                strategy_breakdown[strat] = {"count": 0, "pnl": 0, "wins": 0}
            strategy_breakdown[strat]["count"] += 1
            strategy_breakdown[strat]["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                strategy_breakdown[strat]["wins"] += 1

        for strat, data in strategy_breakdown.items():
            data["win_rate"] = round(data["wins"] / data["count"] * 100, 1)
            data["pnl"] = round(data["pnl"], 2)

        # テスタ基準での評価
        testa_evaluation = self._evaluate_testa_standards(
            win_rate, profit_factor, max_drawdown_pct, sharpe, expectancy
        )

        return {
            "symbol": symbol,
            "total_trades": len(trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "total_return_pct": round(total_return_pct, 2),
            "initial_capital": initial_capital,
            "final_capital": round(final_capital, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "∞",
            "max_drawdown": round(max_drawdown, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "sharpe_ratio": round(sharpe, 2),
            "expectancy": round(expectancy, 2),
            "is_statistically_valid": is_statistically_valid,
            "exit_reasons": exit_reasons,
            "strategy_breakdown": strategy_breakdown,
            "trades": trades,
            "testa_evaluation": testa_evaluation,
        }

    def _calculate_max_drawdown(
        self, portfolio_history: List[Dict], initial_capital: float
    ) -> tuple:
        """最大ドローダウンを計算する"""
        if not portfolio_history:
            return 0, 0

        values = [initial_capital] + [h["portfolio_value"] for h in portfolio_history]
        peak = values[0]
        max_dd = 0
        max_dd_pct = 0

        for value in values:
            if value > peak:
                peak = value
            dd = peak - value
            dd_pct = dd / peak * 100
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = dd_pct

        return max_dd, max_dd_pct

    def _calculate_sharpe_ratio(self, pnls: List[float], initial_capital: float, risk_free_rate: float = 0.05) -> float:
        """シャープレシオを計算する（年率換算）"""
        if len(pnls) < 2:
            return 0

        returns = [p / initial_capital for p in pnls]
        avg_return = sum(returns) / len(returns)
        std_return = pd.Series(returns).std()

        if std_return < 1e-10:
            return 0

        daily_rf = risk_free_rate / 252
        sharpe = (avg_return - daily_rf) / std_return * math.sqrt(252)
        return sharpe

    def _evaluate_testa_standards(
        self,
        win_rate: float,
        profit_factor: float,
        max_drawdown_pct: float,
        sharpe: float,
        expectancy: float,
    ) -> Dict:
        """
        テスタ基準で戦略を評価する
        「負けないことを最優先」の観点から戦略の安全性を評価
        """
        score = 0
        comments = []
        grade = "F"

        # 勝率評価（テスタ氏は勝率より期待値を重視するが、基準として）
        if win_rate >= 55:
            score += 25
            comments.append(f"✅ 勝率{win_rate:.0f}% (良好)")
        elif win_rate >= 45:
            score += 15
            comments.append(f"⚠️ 勝率{win_rate:.0f}% (要改善)")
        else:
            comments.append(f"❌ 勝率{win_rate:.0f}% (不十分)")

        # プロフィットファクター（2.0以上が理想）
        pf = profit_factor if isinstance(profit_factor, float) else 999
        if pf >= 2.0:
            score += 25
            comments.append(f"✅ PF={pf:.2f} (優秀)")
        elif pf >= 1.5:
            score += 15
            comments.append(f"⚠️ PF={pf:.2f} (良好)")
        elif pf >= 1.0:
            score += 5
            comments.append(f"⚠️ PF={pf:.2f} (微益)")
        else:
            comments.append(f"❌ PF={pf:.2f} (損失戦略)")

        # 最大ドローダウン（テスタ哲学: 損失最小化最優先）
        if max_drawdown_pct <= 10:
            score += 25
            comments.append(f"✅ 最大DD={max_drawdown_pct:.1f}% (優秀)")
        elif max_drawdown_pct <= 20:
            score += 15
            comments.append(f"⚠️ 最大DD={max_drawdown_pct:.1f}% (許容範囲)")
        elif max_drawdown_pct <= 30:
            score += 5
            comments.append(f"⚠️ 最大DD={max_drawdown_pct:.1f}% (要注意)")
        else:
            comments.append(f"❌ 最大DD={max_drawdown_pct:.1f}% (危険)")

        # シャープレシオ
        if sharpe >= 1.5:
            score += 25
            comments.append(f"✅ シャープ={sharpe:.2f} (優秀)")
        elif sharpe >= 1.0:
            score += 15
            comments.append(f"⚠️ シャープ={sharpe:.2f} (良好)")
        elif sharpe >= 0.5:
            score += 5
            comments.append(f"⚠️ シャープ={sharpe:.2f} (普通)")
        else:
            comments.append(f"❌ シャープ={sharpe:.2f} (不良)")

        if score >= 85:
            grade = "A"
        elif score >= 70:
            grade = "B"
        elif score >= 55:
            grade = "C"
        elif score >= 40:
            grade = "D"
        else:
            grade = "F"

        return {
            "grade": grade,
            "score": score,
            "comments": comments,
            "recommendation": self._get_recommendation(grade, max_drawdown_pct, profit_factor),
        }

    def _get_recommendation(self, grade: str, max_dd: float, pf) -> str:
        pf_val = pf if isinstance(pf, float) else 999
        if grade == "A":
            return "本番取引可能。設定通りのポジションサイズで実行を推奨します。"
        elif grade == "B":
            return "ポジションサイズを推奨値の70%に抑えて本番テストを推奨します。"
        elif grade == "C":
            return "さらなる最適化が必要。戦略パラメータを見直してください。"
        elif grade == "D":
            return "本番取引不推奨。根本的な戦略の見直しが必要です。"
        else:
            return "この戦略での取引は推奨しません。完全な見直しが必要です。"

    def format_report(self, results: Dict) -> str:
        """バックテスト結果を読みやすいテキスト形式でフォーマットする"""
        if "error" in results:
            return f"⚠️ バックテストエラー: {results['error']}"

        # 取引数0件の場合は簡易レポートを返す
        if results.get("total_trades", 0) == 0:
            return (
                f"{'='*50}\n"
                f"📊 バックテスト結果: {results.get('symbol', '?')}\n"
                f"{'='*50}\n"
                f"⚠️ バックテスト期間中に条件を満たす取引シグナルがありませんでした\n"
                f"（戦略パラメータの緩和またはバックテスト期間の変更を検討してください）"
            )

        eval_data = results.get("testa_evaluation", {})
        grade = eval_data.get("grade", "N/A")
        score = eval_data.get("score", 0)

        lines = [
            f"{'='*50}",
            f"📊 バックテスト結果: {results['symbol']}",
            f"{'='*50}",
            f"【テスタ基準評価】 グレード: {grade} ({score}/100点)",
            f"",
            f"📈 パフォーマンス",
            f"  総取引数: {results['total_trades']}回",
            f"  勝率:    {results.get('win_rate', 0)}%",
            f"  総損益:  ${results.get('total_pnl', 0):+.2f}",
            f"  リターン: {results.get('total_return_pct', 0):+.1f}%",
            f"",
            f"🛡️ リスク指標",
            f"  最大DD:  -{results['max_drawdown_pct']:.1f}%",
            f"  PF:      {results['profit_factor']}",
            f"  シャープ: {results['sharpe_ratio']:.2f}",
            f"",
            f"📋 取引統計",
            f"  平均利益: ${results['avg_win']:.2f}",
            f"  平均損失: ${results['avg_loss']:.2f}",
            f"  期待値:  ${results['expectancy']:.2f}/取引",
            f"",
        ]

        if eval_data.get("comments"):
            lines.append("📝 評価コメント")
            for comment in eval_data["comments"]:
                lines.append(f"  {comment}")

        lines.append("")
        rec = eval_data.get("recommendation", "")
        if rec:
            lines.append(f"💡 推奨: {rec}")

        if not results.get("is_statistically_valid"):
            lines.append(f"")
            lines.append(f"⚠️ 注意: 取引数が{results['total_trades']}回と少なく、統計的信頼性が低い可能性があります")

        return "\n".join(lines)
