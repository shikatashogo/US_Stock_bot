"""
backtest/metrics.py のユニットテスト
修正箇所: self.settings_min_trades の不正な自己代入削除
"""
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backtest.metrics import BacktestMetrics


@pytest.fixture
def metrics():
    return BacktestMetrics()


def _make_trades(wins: int, losses: int, win_pnl=100.0, loss_pnl=-50.0):
    """テスト用のトレードリストを生成する"""
    trades = []
    for i in range(wins):
        trades.append({"pnl": win_pnl, "strategy": "test", "exit_reason": "take_profit"})
    for i in range(losses):
        trades.append({"pnl": loss_pnl, "strategy": "test", "exit_reason": "stop_loss"})
    return trades


def _make_portfolio_history(n=10, base=10_000, growth=100):
    return [{"portfolio_value": base + i * growth} for i in range(n)]


# ---------------------------------------------------------------
# self.settings_min_trades バグ修正の検証
# ---------------------------------------------------------------

class TestMinTradesBugFix:

    def test_no_settings_min_trades_attribute(self, metrics):
        """修正前のバグ: self.settings_min_trades が意図せず作られていた"""
        trades = _make_trades(wins=5, losses=5)
        portfolio = _make_portfolio_history()
        metrics.calculate(trades, portfolio, initial_capital=10_000)
        # 修正後: self.settings_min_trades は存在しないはず
        assert not hasattr(metrics, "settings_min_trades")

    def test_is_statistically_valid_true_when_30_trades(self, metrics):
        trades = _make_trades(wins=20, losses=10)  # 合計30件
        portfolio = _make_portfolio_history()
        result = metrics.calculate(trades, portfolio, initial_capital=10_000)
        assert result["is_statistically_valid"] is True

    def test_is_statistically_valid_false_when_less_than_30(self, metrics):
        trades = _make_trades(wins=10, losses=5)  # 合計15件
        portfolio = _make_portfolio_history()
        result = metrics.calculate(trades, portfolio, initial_capital=10_000)
        assert result["is_statistically_valid"] is False


# ---------------------------------------------------------------
# calculate テスト
# ---------------------------------------------------------------

class TestCalculate:

    def test_empty_trades_returns_zero_total(self, metrics):
        result = metrics.calculate([], [], initial_capital=10_000, symbol="AAPL")
        assert result["total_trades"] == 0

    def test_win_rate_calculation(self, metrics):
        trades = _make_trades(wins=7, losses=3)
        result = metrics.calculate(trades, _make_portfolio_history(), 10_000)
        assert result["win_rate"] == pytest.approx(70.0, abs=0.1)

    def test_total_pnl_is_sum_of_pnls(self, metrics):
        trades = _make_trades(wins=3, losses=2, win_pnl=100.0, loss_pnl=-50.0)
        result = metrics.calculate(trades, _make_portfolio_history(), 10_000)
        # 3 * 100 + 2 * (-50) = 200
        assert result["total_pnl"] == pytest.approx(200.0, abs=0.01)

    def test_profit_factor_positive(self, metrics):
        trades = _make_trades(wins=5, losses=3, win_pnl=100.0, loss_pnl=-50.0)
        result = metrics.calculate(trades, _make_portfolio_history(), 10_000)
        # PF = 500 / 150 ≈ 3.33
        assert result["profit_factor"] == pytest.approx(3.33, abs=0.1)

    def test_profit_factor_infinite_when_no_losses(self, metrics):
        trades = _make_trades(wins=5, losses=0)
        result = metrics.calculate(trades, _make_portfolio_history(), 10_000)
        assert result["profit_factor"] == "∞"

    def test_final_capital_equals_initial_plus_pnl(self, metrics):
        trades = _make_trades(wins=3, losses=1, win_pnl=100.0, loss_pnl=-50.0)
        result = metrics.calculate(trades, _make_portfolio_history(), 10_000)
        # 3*100 - 50 = 250
        assert result["final_capital"] == pytest.approx(10_250.0, abs=0.01)

    def test_symbol_is_propagated(self, metrics):
        result = metrics.calculate(_make_trades(1, 0), _make_portfolio_history(), 10_000, symbol="TSLA")
        assert result["symbol"] == "TSLA"

    def test_exit_reasons_are_counted(self, metrics):
        trades = [
            {"pnl": 100, "strategy": "test", "exit_reason": "take_profit"},
            {"pnl": 100, "strategy": "test", "exit_reason": "take_profit"},
            {"pnl": -50, "strategy": "test", "exit_reason": "stop_loss"},
        ]
        result = metrics.calculate(trades, _make_portfolio_history(), 10_000)
        assert result["exit_reasons"]["take_profit"] == 2
        assert result["exit_reasons"]["stop_loss"] == 1


# ---------------------------------------------------------------
# 最大ドローダウンテスト
# ---------------------------------------------------------------

class TestMaxDrawdown:

    def test_no_drawdown_when_monotonically_increasing(self, metrics):
        history = [{"portfolio_value": 10_000 + i * 100} for i in range(10)]
        dd, dd_pct = metrics._calculate_max_drawdown(history, initial_capital=10_000)
        assert dd == 0
        assert dd_pct == 0

    def test_drawdown_calculated_correctly(self, metrics):
        # 10000 → 12000 → 9000 → 11000: 最大DDは 12000-9000=3000
        history = [
            {"portfolio_value": 10_000},
            {"portfolio_value": 12_000},
            {"portfolio_value": 9_000},
            {"portfolio_value": 11_000},
        ]
        dd, dd_pct = metrics._calculate_max_drawdown(history, initial_capital=10_000)
        assert dd == pytest.approx(3_000, abs=1)
        assert dd_pct == pytest.approx(25.0, abs=0.1)

    def test_empty_history_returns_zero(self, metrics):
        dd, dd_pct = metrics._calculate_max_drawdown([], initial_capital=10_000)
        assert dd == 0
        assert dd_pct == 0


# ---------------------------------------------------------------
# シャープレシオテスト
# ---------------------------------------------------------------

class TestSharpeRatio:

    def test_sharpe_zero_with_single_trade(self, metrics):
        sharpe = metrics._calculate_sharpe_ratio([100.0], initial_capital=10_000)
        assert sharpe == 0

    def test_sharpe_zero_with_identical_returns(self, metrics):
        """リターンが全て同じ → 標準偏差=0 → シャープ=0"""
        sharpe = metrics._calculate_sharpe_ratio([100.0] * 10, initial_capital=10_000)
        assert sharpe == 0

    def test_positive_sharpe_with_consistent_gains(self, metrics):
        """一定の利益が続く場合はシャープレシオが正"""
        pnls = [100.0 + i for i in range(20)]  # 右肩上がり
        sharpe = metrics._calculate_sharpe_ratio(pnls, initial_capital=10_000)
        assert sharpe > 0


# ---------------------------------------------------------------
# テスタ評価テスト (グレード境界値)
# ---------------------------------------------------------------

class TestTestaEvaluation:

    def test_grade_a_when_all_metrics_excellent(self, metrics):
        result = metrics._evaluate_testa_standards(
            win_rate=60.0,
            profit_factor=2.5,
            max_drawdown_pct=8.0,
            sharpe=1.8,
            expectancy=50.0,
        )
        assert result["grade"] == "A"
        assert result["score"] >= 85

    def test_grade_f_when_all_metrics_poor(self, metrics):
        result = metrics._evaluate_testa_standards(
            win_rate=30.0,
            profit_factor=0.5,
            max_drawdown_pct=40.0,
            sharpe=0.1,
            expectancy=-20.0,
        )
        assert result["grade"] == "F"

    def test_drawdown_above_30pct_triggers_danger(self, metrics):
        result = metrics._evaluate_testa_standards(55, 2.0, 35.0, 1.5, 50.0)
        comments = " ".join(result["comments"])
        assert "危険" in comments

    def test_vix_like_extreme_drawdown_is_grade_d_or_f(self, metrics):
        """最大DD35%超は危険水準でDかF"""
        result = metrics._evaluate_testa_standards(45, 1.2, 35.0, 0.4, 10.0)
        assert result["grade"] in ("D", "F")

    def test_recommendation_is_string(self, metrics):
        result = metrics._evaluate_testa_standards(55, 2.0, 10.0, 1.5, 50.0)
        assert isinstance(result["recommendation"], str)
        assert len(result["recommendation"]) > 0


# ---------------------------------------------------------------
# format_report テスト
# ---------------------------------------------------------------

class TestFormatReport:

    def test_empty_trades_report(self, metrics):
        report = metrics.format_report({"total_trades": 0, "symbol": "AAPL"})
        assert "シグナルがありませんでした" in report

    def test_error_report(self, metrics):
        report = metrics.format_report({"error": "接続失敗"})
        assert "エラー" in report

    def test_full_report_contains_key_metrics(self, metrics):
        trades = _make_trades(wins=20, losses=10, win_pnl=150.0, loss_pnl=-60.0)
        result = metrics.calculate(trades, _make_portfolio_history(), 10_000, symbol="NVDA")
        report = metrics.format_report(result)
        assert "NVDA" in report
        assert "勝率" in report
        assert "シャープ" in report
