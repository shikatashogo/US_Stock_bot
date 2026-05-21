"""
position_sizer.py のユニットテスト
修正箇所: tuple[bool, str] → Tuple[bool, str] (Python 3.8互換)
テスタルール境界値: 損切り2%、最大ポジション30%
"""
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.risk.position_sizer import PositionSizer


@pytest.fixture
def settings():
    return {
        "portfolio": {
            "max_positions": 3,
            "max_position_pct": 0.30,
        },
        "testa_rules": {
            "stop_loss": {
                "default_pct": 0.02,
                "max_pct": 0.03,
            }
        },
    }


@pytest.fixture
def sizer(settings):
    return PositionSizer(settings)


# ---------------------------------------------------------------
# calculate_shares テスト
# ---------------------------------------------------------------

class TestCalculateShares:

    def test_basic_calculation(self, sizer):
        """基本計算: リスク2%、価格$100、ストップ$98"""
        portfolio = 10_000
        price = 100.0
        stop_loss = 98.0  # 2%下
        result = sizer.calculate_shares(portfolio, price, stop_loss)

        # リスク金額 = 10000 * 0.02 = $200
        # リスク/株 = 100 - 98 = $2
        # 理論株数 = 200 / 2 = 100株
        # 最大ポジション = 10000 * 0.30 / 100 = 30株 → min(100, 30) = 30
        assert result["shares"] == 30
        assert result["risk_per_share"] == 2.0

    def test_risk_capped_by_max_position(self, sizer):
        """最大ポジション制限でキャップされること"""
        result = sizer.calculate_shares(
            portfolio_value=10_000,
            price=100.0,
            stop_loss=99.0,  # 1%下: 株数が大きくなる
        )
        max_shares = int(10_000 * 0.30 / 100.0)  # = 30
        assert result["shares"] <= max_shares

    def test_stop_loss_above_price_returns_zero(self, sizer):
        """ストップロス ≥ 価格の場合は0株"""
        result = sizer.calculate_shares(
            portfolio_value=10_000,
            price=100.0,
            stop_loss=101.0,  # 価格より高い
        )
        assert result["shares"] == 0

    def test_zero_price_returns_empty(self, sizer):
        """価格0は不正入力として空結果を返すこと"""
        result = sizer.calculate_shares(
            portfolio_value=10_000,
            price=0.0,
            stop_loss=0.0,
        )
        assert result["shares"] == 0

    def test_volume_multiplier_reduces_shares(self, sizer):
        """volume_multiplier=0.5 でポジションが半減すること"""
        result_full = sizer.calculate_shares(10_000, 100.0, 98.0, volume_multiplier=1.0)
        result_half = sizer.calculate_shares(10_000, 100.0, 98.0, volume_multiplier=0.5)
        assert result_half["shares"] <= result_full["shares"]

    def test_risk_pct_override(self, sizer):
        """risk_pct を明示指定した場合、デフォルト(2%)より使われること"""
        result_default = sizer.calculate_shares(10_000, 100.0, 98.0)
        result_custom = sizer.calculate_shares(10_000, 100.0, 98.0, risk_pct=0.03)
        # 3%リスクの方がポジションが大きくなる（ただしmax_position制限あり）
        assert result_custom["shares"] >= result_default["shares"]

    def test_position_value_equals_shares_times_price(self, sizer):
        """position_value = shares × price"""
        result = sizer.calculate_shares(10_000, 150.0, 147.0)
        assert result["position_value"] == pytest.approx(result["shares"] * 150.0, rel=1e-6)

    def test_risk_amount_equals_shares_times_risk_per_share(self, sizer):
        """risk_amount = shares × (price - stop_loss)"""
        result = sizer.calculate_shares(10_000, 100.0, 98.0)
        expected_risk = result["shares"] * 2.0
        assert result["risk_amount"] == pytest.approx(expected_risk, rel=1e-6)

    def test_testa_2pct_stop_does_not_exceed_max_position(self, sizer):
        """テスタルール: 損切り2%でも最大ポジション30%を超えないこと"""
        result = sizer.calculate_shares(50_000, 200.0, 196.0)
        max_position_value = 50_000 * 0.30
        assert result["position_value"] <= max_position_value + 0.01  # 浮動小数点許容

    def test_small_portfolio_may_return_zero(self, sizer):
        """極端に小さいポートフォリオは0株になりうる（エラーにならないこと）"""
        result = sizer.calculate_shares(100.0, 500.0, 490.0)
        assert result["shares"] == 0
        assert result["risk_amount"] == 0


# ---------------------------------------------------------------
# can_open_position テスト (型ヒント修正の動作確認)
# ---------------------------------------------------------------

class TestCanOpenPosition:

    def test_can_open_when_conditions_met(self, sizer):
        ok, msg = sizer.can_open_position(
            portfolio_value=10_000,
            current_positions=0,
            shares=10,
            price=100.0,
            cash_balance=5_000,
        )
        assert ok is True
        assert "可能" in msg

    def test_blocked_by_max_positions(self, sizer):
        ok, msg = sizer.can_open_position(
            portfolio_value=10_000,
            current_positions=3,  # max_positions=3
            shares=10,
            price=100.0,
            cash_balance=5_000,
        )
        assert ok is False
        assert "最大ポジション数" in msg

    def test_blocked_by_insufficient_cash(self, sizer):
        ok, msg = sizer.can_open_position(
            portfolio_value=10_000,
            current_positions=1,
            shares=100,
            price=100.0,
            cash_balance=500,   # 必要額 $10,000 > 残高 $500
        )
        assert ok is False
        assert "現金不足" in msg

    def test_blocked_by_zero_shares(self, sizer):
        ok, msg = sizer.can_open_position(
            portfolio_value=10_000,
            current_positions=0,
            shares=0,
            price=100.0,
            cash_balance=5_000,
        )
        assert ok is False
        assert "0株" in msg

    def test_return_type_is_tuple(self, sizer):
        """修正後の型ヒントが実際にタプルを返すこと"""
        result = sizer.can_open_position(10_000, 0, 10, 100.0, 5_000)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)
