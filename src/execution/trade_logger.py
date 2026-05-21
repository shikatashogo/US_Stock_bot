"""
取引ログ管理モジュール
全取引をSQLiteデータベースに記録する
バックテスト・パフォーマンス分析・日次レポートに使用
"""
import sqlite3
from typing import List, Dict, Optional
from datetime import datetime, date, timedelta
from pathlib import Path
import pandas as pd
import pytz
from loguru import logger


DB_PATH = Path("data/trades.db")

CREATE_TRADES_TABLE = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    trade_type TEXT NOT NULL,          -- day / swing
    action TEXT NOT NULL,              -- BUY / SELL / CLOSE
    shares INTEGER NOT NULL,
    entry_price REAL,
    exit_price REAL,
    stop_loss REAL,
    take_profit REAL,
    pnl REAL,
    pnl_pct REAL,
    commission REAL DEFAULT 0,
    entry_time TEXT,
    exit_time TEXT,
    hold_minutes INTEGER,
    status TEXT DEFAULT 'open',        -- open / closed / cancelled
    signal_reason TEXT,
    testa_score REAL,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_DAILY_STATS_TABLE = """
CREATE TABLE IF NOT EXISTS daily_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT UNIQUE NOT NULL,
    portfolio_value REAL,
    daily_pnl REAL,
    daily_pnl_pct REAL,
    trades_count INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    win_rate REAL,
    avg_win REAL,
    avg_loss REAL,
    max_drawdown REAL,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


class TradeLogger:
    """取引記録を管理するクラス"""

    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self.et_tz = pytz.timezone("America/New_York")

    def _init_db(self):
        """データベースを初期化する"""
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(CREATE_TRADES_TABLE)
            conn.execute(CREATE_DAILY_STATS_TABLE)
            conn.commit()
        logger.debug(f"取引DB初期化: {DB_PATH}")

    def log_entry(
        self,
        symbol: str,
        strategy: str,
        trade_type: str,
        shares: int,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        signal_reason: str = "",
        testa_score: float = 0.0,
    ) -> int:
        """エントリー（買い）を記録する。取引IDを返す"""
        now = datetime.now(self.et_tz).isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute("""
                INSERT INTO trades
                (symbol, strategy, trade_type, action, shares, entry_price,
                 stop_loss, take_profit, entry_time, status, signal_reason, testa_score)
                VALUES (?, ?, ?, 'BUY', ?, ?, ?, ?, ?, 'open', ?, ?)
            """, (symbol, strategy, trade_type, shares, entry_price,
                  stop_loss, take_profit, now, signal_reason, testa_score))
            trade_id = cursor.lastrowid
            conn.commit()

        logger.info(f"取引ログ[エントリー] ID:{trade_id} {symbol} {shares}株 @ ${entry_price:.2f}")
        return trade_id

    def log_exit(
        self,
        trade_id: int,
        exit_price: float,
        exit_reason: str = "",
        commission: float = 0.0,
    ) -> Dict:
        """エグジット（売り）を記録してPnLを計算する"""
        now = datetime.now(self.et_tz).isoformat()

        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE id = ? AND status = 'open'", (trade_id,)
            ).fetchone()

            if not row:
                logger.error(f"未クローズの取引ID {trade_id} が見つかりません")
                return {}

            cols = [d[0] for d in conn.execute("SELECT * FROM trades LIMIT 0").description]
            trade = dict(zip(cols, row))

            pnl = (exit_price - trade["entry_price"]) * trade["shares"] - commission
            pnl_pct = pnl / (trade["entry_price"] * trade["shares"])

            entry_time = datetime.fromisoformat(trade["entry_time"])
            exit_time = datetime.fromisoformat(now)
            hold_minutes = int((exit_time - entry_time).total_seconds() / 60)

            conn.execute("""
                UPDATE trades
                SET exit_price=?, exit_time=?, pnl=?, pnl_pct=?,
                    commission=?, hold_minutes=?, status='closed', notes=?
                WHERE id=?
            """, (exit_price, now, pnl, pnl_pct, commission, hold_minutes, exit_reason, trade_id))
            conn.commit()

        result = {
            "trade_id": trade_id,
            "symbol": trade["symbol"],
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct * 100, 2),
            "hold_minutes": hold_minutes,
            "exit_reason": exit_reason,
        }
        emoji = "✅" if pnl >= 0 else "❌"
        logger.info(
            f"取引ログ[エグジット] {emoji} ID:{trade_id} {trade['symbol']} "
            f"PnL: ${pnl:.2f} ({pnl_pct*100:.1f}%)"
        )
        return result

    def save_daily_stats(self, portfolio_value: float, daily_pnl: float):
        """日次統計を保存する"""
        # ET（米国東部時間）の日付を使用（取引ログはETで保存されているため）
        et_tz = pytz.timezone("America/New_York")
        today = datetime.now(et_tz).date().isoformat()

        daily_trades = self.get_trades_for_date(today)
        trades_count = len(daily_trades)
        winning = [t for t in daily_trades if t.get("pnl", 0) > 0]
        losing = [t for t in daily_trades if t.get("pnl", 0) < 0]

        win_rate = len(winning) / trades_count * 100 if trades_count > 0 else 0
        avg_win = sum(t["pnl"] for t in winning) / len(winning) if winning else 0
        avg_loss = sum(t["pnl"] for t in losing) / len(losing) if losing else 0
        daily_pnl_pct = (daily_pnl / (portfolio_value - daily_pnl)) * 100 if (portfolio_value - daily_pnl) > 0 else 0

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO daily_stats
                (trade_date, portfolio_value, daily_pnl, daily_pnl_pct,
                 trades_count, winning_trades, losing_trades, win_rate, avg_win, avg_loss)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (today, portfolio_value, daily_pnl, daily_pnl_pct,
                  trades_count, len(winning), len(losing), win_rate, avg_win, avg_loss))
            conn.commit()

        logger.info(f"日次統計保存: {today} | PnL: ${daily_pnl:.2f} | 勝率: {win_rate:.0f}%")

    def get_trades_for_date(self, trade_date: str) -> List[Dict]:
        """特定日の取引を取得する"""
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades WHERE DATE(entry_time) = ? ORDER BY entry_time",
                (trade_date,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_open_trades(self) -> List[Dict]:
        """未クローズの取引を取得する"""
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = 'open' ORDER BY entry_time"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_performance_stats(self, days: int = 30) -> Dict:
        """直近N日間のパフォーマンス統計を取得する"""
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute("""
                SELECT pnl, pnl_pct, hold_minutes, strategy, symbol
                FROM trades
                WHERE status = 'closed'
                  AND entry_time >= datetime('now', ?)
                ORDER BY entry_time DESC
            """, (f"-{days} days",)).fetchall()

        if not rows:
            return {"total_trades": 0}

        pnls = [r[0] for r in rows if r[0] is not None]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        return {
            "total_trades": len(pnls),
            "total_pnl": round(sum(pnls), 2),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "profit_factor": round(abs(sum(wins)) / abs(sum(losses)), 2) if losses else float("inf"),
            "avg_hold_minutes": round(sum(r[2] for r in rows if r[2]) / len(rows), 0) if rows else 0,
        }

    def get_recent_trades(self, limit: int = 10) -> List[Dict]:
        """直近の取引履歴を取得する"""
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM trades
                WHERE status = 'closed'
                ORDER BY exit_time DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [dict(row) for row in rows]

    def update_stop_loss(self, trade_id: int, new_stop_loss: float) -> bool:
        """
        トレーリングストップ更新時にDBのstop_lossを書き換える
        Bot再起動・他ループ反復でも新しいストップが参照されるようにする
        """
        with sqlite3.connect(DB_PATH) as conn:
            result = conn.execute(
                "UPDATE trades SET stop_loss=? WHERE id=? AND status='open'",
                (new_stop_loss, trade_id)
            )
            conn.commit()
            if result.rowcount == 0:
                logger.warning(f"update_stop_loss: ID {trade_id} が見つからないかすでにクローズ済み")
                return False
        logger.debug(f"取引ID {trade_id} のストップロスをDB更新: ${new_stop_loss:.2f}")
        return True

    def mark_as_cancelled(self, trade_id: int, reason: str = "") -> bool:
        """
        取引をキャンセル済みとしてマークする
        （直接SQLiteを叩く代わりにこのメソッドを使用する）
        """
        with sqlite3.connect(DB_PATH) as conn:
            result = conn.execute(
                "UPDATE trades SET status='cancelled', notes=? WHERE id=? AND status='open'",
                (reason, trade_id)
            )
            conn.commit()
            if result.rowcount == 0:
                logger.warning(f"mark_as_cancelled: ID {trade_id} が見つからないかすでにクローズ/キャンセル済み")
                return False
        logger.info(f"取引ID {trade_id} をキャンセル済みに更新: {reason}")
        return True

    def count_day_trades_this_week(self) -> int:
        """
        今週のデイトレード回数を返す（PDTルール管理用）
        月曜日を週の開始として集計する
        """
        et_tz = pytz.timezone("America/New_York")
        now = datetime.now(et_tz)
        # 月曜日の0時0分0秒（ET）を週の開始とする
        # 注: now.day - days_since_monday は月初週でマイナスになるためtimedeltaを使う
        days_since_monday = now.weekday()  # 0=月, 6=日
        week_start = (now - timedelta(days=days_since_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        week_start_str = week_start.isoformat()

        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("""
                SELECT COUNT(*) FROM trades
                WHERE trade_type = 'day'
                  AND status IN ('open', 'closed')
                  AND entry_time >= ?
            """, (week_start_str,)).fetchone()
        count = row[0] if row else 0
        return count
