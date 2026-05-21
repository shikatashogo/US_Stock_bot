"""
trade_logger.py のユニットテスト
修正箇所: ISO形式のdatetimeパース (fromisoformat)
テスト方針: 実際のSQLiteを使うが、テスト専用のインメモリDBを使用
"""
import pytest
import sqlite3
import pytz
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.execution.trade_logger import TradeLogger, DB_PATH


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """テスト専用の一時DBを使用するフィクスチャ"""
    test_db = tmp_path / "test_trades.db"
    monkeypatch.setattr("src.execution.trade_logger.DB_PATH", test_db)
    logger = TradeLogger()
    return logger


# ---------------------------------------------------------------
# log_entry テスト
# ---------------------------------------------------------------

class TestLogEntry:

    def test_entry_returns_valid_id(self, tmp_db):
        trade_id = tmp_db.log_entry(
            symbol="AAPL",
            strategy="ma_crossover",
            trade_type="swing",
            shares=10,
            entry_price=150.0,
            stop_loss=147.0,
            take_profit=156.0,
        )
        assert isinstance(trade_id, int)
        assert trade_id > 0

    def test_entry_is_stored_as_open(self, tmp_db):
        trade_id = tmp_db.log_entry("MSFT", "breakout", "swing", 5, 300.0, 294.0, 312.0)
        open_trades = tmp_db.get_open_trades()
        ids = [t["id"] for t in open_trades]
        assert trade_id in ids

    def test_entry_time_has_timezone(self, tmp_db):
        trade_id = tmp_db.log_entry("NVDA", "rsi_pullback", "swing", 3, 500.0, 490.0, 520.0)
        open_trades = tmp_db.get_open_trades()
        trade = next(t for t in open_trades if t["id"] == trade_id)
        # entry_time は ISO形式でタイムゾーン付きであること
        entry_dt = datetime.fromisoformat(trade["entry_time"])
        assert entry_dt.tzinfo is not None


# ---------------------------------------------------------------
# log_exit テスト (P0バグ修正の検証: hold_minutes計算)
# ---------------------------------------------------------------

class TestLogExit:

    def test_exit_calculates_pnl_correctly(self, tmp_db):
        trade_id = tmp_db.log_entry("AAPL", "breakout", "swing", 10, 150.0, 147.0, 156.0)
        result = tmp_db.log_exit(trade_id, exit_price=156.0)

        assert result["trade_id"] == trade_id
        # PnL = (156 - 150) * 10 = $60
        assert result["pnl"] == pytest.approx(60.0, abs=0.01)
        assert result["pnl_pct"] == pytest.approx(4.0, abs=0.1)  # 4% gain

    def test_exit_calculates_loss_correctly(self, tmp_db):
        trade_id = tmp_db.log_entry("AAPL", "breakout", "swing", 10, 150.0, 147.0, 156.0)
        result = tmp_db.log_exit(trade_id, exit_price=147.0)

        # PnL = (147 - 150) * 10 = -$30
        assert result["pnl"] == pytest.approx(-30.0, abs=0.01)

    def test_exit_hold_minutes_is_non_negative(self, tmp_db):
        """修正箇所: hold_minutes が負にならないこと（ISOパース修正の検証）"""
        trade_id = tmp_db.log_entry("MSFT", "vwap_bounce", "day", 5, 300.0, 294.0, 312.0)
        result = tmp_db.log_exit(trade_id, exit_price=305.0)
        assert result["hold_minutes"] >= 0

    def test_exit_closes_the_trade(self, tmp_db):
        trade_id = tmp_db.log_entry("NVDA", "orb", "day", 2, 500.0, 490.0, 520.0)
        tmp_db.log_exit(trade_id, exit_price=510.0)
        open_trades = tmp_db.get_open_trades()
        open_ids = [t["id"] for t in open_trades]
        assert trade_id not in open_ids

    def test_exit_unknown_id_returns_empty(self, tmp_db):
        result = tmp_db.log_exit(99999, exit_price=100.0)
        assert result == {}

    def test_exit_commission_reduces_pnl(self, tmp_db):
        trade_id = tmp_db.log_entry("AAPL", "breakout", "swing", 10, 150.0, 147.0, 156.0)
        result = tmp_db.log_exit(trade_id, exit_price=156.0, commission=5.0)
        # PnL = (156 - 150) * 10 - 5 = $55
        assert result["pnl"] == pytest.approx(55.0, abs=0.01)

    def test_iso_parse_with_et_timezone(self, tmp_db):
        """タイムゾーン付きISOフォーマット (例: 2024-01-15T09:30:00-05:00) のパースが成功すること"""
        et_tz = pytz.timezone("America/New_York")
        iso_with_tz = datetime.now(et_tz).isoformat()
        # fromisoformat でパースできること
        parsed = datetime.fromisoformat(iso_with_tz)
        assert parsed.tzinfo is not None

    def test_iso_parse_subtraction_works_same_tz(self):
        """同一タイムゾーン同士のdatetime差分が正常に計算されること"""
        et_tz = pytz.timezone("America/New_York")
        entry = datetime.now(et_tz) - timedelta(hours=2)
        exit_ = datetime.now(et_tz)
        diff_minutes = int((exit_ - entry).total_seconds() / 60)
        assert diff_minutes == pytest.approx(120, abs=1)


# ---------------------------------------------------------------
# update_stop_loss テスト
# ---------------------------------------------------------------

class TestUpdateStopLoss:

    def test_update_stop_loss_success(self, tmp_db):
        trade_id = tmp_db.log_entry("AAPL", "trailing", "swing", 10, 150.0, 147.0, 156.0)
        result = tmp_db.update_stop_loss(trade_id, 149.0)
        assert result is True

    def test_update_stop_loss_unknown_id(self, tmp_db):
        result = tmp_db.update_stop_loss(99999, 100.0)
        assert result is False


# ---------------------------------------------------------------
# mark_as_cancelled テスト
# ---------------------------------------------------------------

class TestMarkAsCancelled:

    def test_cancel_open_trade(self, tmp_db):
        trade_id = tmp_db.log_entry("TSLA", "breakout", "swing", 5, 200.0, 196.0, 210.0)
        result = tmp_db.mark_as_cancelled(trade_id, "テスト中断")
        assert result is True

    def test_cancelled_trade_not_in_open(self, tmp_db):
        trade_id = tmp_db.log_entry("TSLA", "breakout", "swing", 5, 200.0, 196.0, 210.0)
        tmp_db.mark_as_cancelled(trade_id)
        open_trades = tmp_db.get_open_trades()
        assert trade_id not in [t["id"] for t in open_trades]


# ---------------------------------------------------------------
# PDTルール管理テスト
# ---------------------------------------------------------------

class TestPDTTracking:

    def test_day_trade_counted(self, tmp_db):
        count_before = tmp_db.count_day_trades_this_week()
        tmp_db.log_entry("AMD", "orb", "day", 10, 100.0, 98.0, 104.0)
        count_after = tmp_db.count_day_trades_this_week()
        assert count_after == count_before + 1

    def test_swing_trade_not_counted_as_day(self, tmp_db):
        count_before = tmp_db.count_day_trades_this_week()
        tmp_db.log_entry("AMD", "ma_crossover", "swing", 10, 100.0, 98.0, 104.0)
        count_after = tmp_db.count_day_trades_this_week()
        assert count_after == count_before  # スイングは増えない


# ---------------------------------------------------------------
# get_performance_stats テスト
# ---------------------------------------------------------------

class TestGetPerformanceStats:

    def test_empty_db_returns_zero_trades(self, tmp_db):
        stats = tmp_db.get_performance_stats(days=30)
        assert stats.get("total_trades", 0) == 0

    def test_stats_after_trades(self, tmp_db):
        # 勝ち2回、負け1回
        for entry, exit_, pnl_sign in [
            (150.0, 156.0, 1), (150.0, 156.0, 1), (150.0, 147.0, -1)
        ]:
            tid = tmp_db.log_entry("AAPL", "test", "swing", 10, entry, 147.0, 156.0)
            tmp_db.log_exit(tid, exit_)

        stats = tmp_db.get_performance_stats(days=30)
        assert stats["total_trades"] == 3
        assert stats["win_count"] == 2
        assert stats["loss_count"] == 1
        assert stats["win_rate"] == pytest.approx(66.7, abs=0.5)
