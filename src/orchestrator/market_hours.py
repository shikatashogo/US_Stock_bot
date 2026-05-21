"""
市場時間管理モジュール
米国株市場のオープン・クローズ・祝日を管理する
"""
from datetime import datetime, time, date, timedelta
from typing import Optional
import pytz
from loguru import logger


# 米国市場の主要祝日（2024-2025年）
US_MARKET_HOLIDAYS = {
    # 2024年
    date(2024, 1, 1),   # 元日
    date(2024, 1, 15),  # マーティン・ルーサー・キング記念日
    date(2024, 2, 19),  # 大統領の日
    date(2024, 3, 29),  # グッドフライデー
    date(2024, 5, 27),  # メモリアルデー
    date(2024, 6, 19),  # ジューンティーンス
    date(2024, 7, 4),   # 独立記念日
    date(2024, 9, 2),   # レイバーデー
    date(2024, 11, 28), # サンクスギビング
    date(2024, 12, 25), # クリスマス
    # 2025年
    date(2025, 1, 1),   # 元日
    date(2025, 1, 9),   # ジミー・カーター国家追悼日
    date(2025, 1, 20),  # マーティン・ルーサー・キング記念日
    date(2025, 2, 17),  # 大統領の日
    date(2025, 4, 18),  # グッドフライデー
    date(2025, 5, 26),  # メモリアルデー
    date(2025, 6, 19),  # ジューンティーンス
    date(2025, 7, 4),   # 独立記念日
    date(2025, 9, 1),   # レイバーデー
    date(2025, 11, 27), # サンクスギビング
    date(2025, 12, 25), # クリスマス
    # 2026年
    date(2026, 1, 1),   # 元日
    date(2026, 1, 19),  # マーティン・ルーサー・キング記念日
    date(2026, 2, 16),  # 大統領の日
    date(2026, 4, 3),   # グッドフライデー
    date(2026, 5, 25),  # メモリアルデー
    date(2026, 6, 19),  # ジューンティーンス
    date(2026, 7, 3),   # 独立記念日（観測日）
    date(2026, 9, 7),   # レイバーデー
    date(2026, 11, 26), # サンクスギビング
    date(2026, 12, 25), # クリスマス
}

MARKET_OPEN = time(9, 30)   # 米国東部時間 9:30 AM
MARKET_CLOSE = time(16, 0)  # 米国東部時間 4:00 PM
PRE_MARKET_START = time(4, 0)
AFTER_HOURS_END = time(20, 0)


class MarketHoursManager:
    """米国株市場の時間を管理するクラス"""

    def __init__(self):
        self.et_tz = pytz.timezone("America/New_York")
        self.jst_tz = pytz.timezone("Asia/Tokyo")

    def is_market_open(self) -> bool:
        """現在、米国株市場が開いているか確認する"""
        now = datetime.now(self.et_tz)
        return self._is_trading_day(now.date()) and (MARKET_OPEN <= now.time() < MARKET_CLOSE)

    def is_trading_day(self, check_date: Optional[date] = None) -> bool:
        """指定日（省略時は今日）が取引日か確認する"""
        target = check_date or datetime.now(self.et_tz).date()
        return self._is_trading_day(target)

    def _is_trading_day(self, check_date: date) -> bool:
        """平日かつ祝日でないか確認する"""
        # 土曜日(5)・日曜日(6)は市場なし
        if check_date.weekday() >= 5:
            return False
        # 祝日チェック
        if check_date in US_MARKET_HOLIDAYS:
            return False
        return True

    def time_to_market_open(self) -> Optional[timedelta]:
        """市場オープンまでの時間を返す（市場が既に開いている場合はNone）"""
        now = datetime.now(self.et_tz)

        if self.is_market_open():
            return None

        if now.time() < MARKET_OPEN and self._is_trading_day(now.date()):
            open_today = now.replace(hour=9, minute=30, second=0, microsecond=0)
            return open_today - now

        # 次の取引日を探す
        next_day = now.date() + timedelta(days=1)
        while not self._is_trading_day(next_day):
            next_day += timedelta(days=1)

        next_open = datetime(
            next_day.year, next_day.month, next_day.day, 9, 30, 0,
            tzinfo=self.et_tz
        )
        return next_open - now

    def time_to_market_close(self) -> Optional[timedelta]:
        """市場クローズまでの時間を返す（市場が閉まっている場合はNone）"""
        if not self.is_market_open():
            return None
        now = datetime.now(self.et_tz)
        close_today = now.replace(hour=16, minute=0, second=0, microsecond=0)
        return close_today - now

    def get_next_trading_day(self) -> date:
        """次の取引日を返す"""
        tomorrow = datetime.now(self.et_tz).date() + timedelta(days=1)
        while not self._is_trading_day(tomorrow):
            tomorrow += timedelta(days=1)
        return tomorrow

    def get_market_status_str(self) -> str:
        """市場状態を日本語文字列で返す"""
        now = datetime.now(self.et_tz)
        jst_now = datetime.now(self.jst_tz)

        if self.is_market_open():
            close_in = self.time_to_market_close()
            hours = int(close_in.total_seconds() // 3600)
            minutes = int((close_in.total_seconds() % 3600) // 60)
            return f"開場中（クローズまで {hours}時間{minutes}分） [ET: {now.strftime('%H:%M')} / JST: {jst_now.strftime('%H:%M')}]"
        else:
            open_in = self.time_to_market_open()
            if open_in:
                hours = int(open_in.total_seconds() // 3600)
                minutes = int((open_in.total_seconds() % 3600) // 60)
                return f"閉場中（オープンまで {hours}時間{minutes}分） [JST: {jst_now.strftime('%H:%M')}]"
            return "閉場中"

    def is_pre_market(self) -> bool:
        """プレマーケット時間帯か確認する"""
        now = datetime.now(self.et_tz)
        return (self._is_trading_day(now.date()) and
                PRE_MARKET_START <= now.time() < MARKET_OPEN)

    def is_after_hours(self) -> bool:
        """アフターアワーズ時間帯か確認する"""
        now = datetime.now(self.et_tz)
        return (self._is_trading_day(now.date()) and
                MARKET_CLOSE <= now.time() < AFTER_HOURS_END)

    def is_high_volatility_event_day(self) -> tuple:
        """
        高ボラティリティイベント日（雇用統計・FOMC等）かを判定する
        戻り値: (is_event_day: bool, event_name: str)

        毎月第1金曜日は雇用統計（NFP）。
        FOMCは年8回（公表日）。2026年分のみハードコード。
        """
        today = datetime.now(self.et_tz).date()

        # 雇用統計（毎月第1金曜日）
        if today.weekday() == 4 and today.day <= 7:
            return True, "雇用統計（NFP）発表日"

        # FOMC公表日（2026年予定）
        # 出典: federalreserve.gov の公表スケジュール
        fomc_2026 = {
            date(2026, 1, 28),  # 1月会合
            date(2026, 3, 18),  # 3月会合
            date(2026, 4, 29),  # 4月会合
            date(2026, 6, 17),  # 6月会合
            date(2026, 7, 29),  # 7月会合
            date(2026, 9, 16),  # 9月会合
            date(2026, 10, 28), # 10月会合
            date(2026, 12, 9),  # 12月会合
        }
        if today in fomc_2026:
            return True, "FOMC政策金利発表日"

        return False, ""
