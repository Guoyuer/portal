"""Extended tests for validate.py edge cases."""
from __future__ import annotations

from pathlib import Path

import pytest

from etl.db import get_connection
from etl.validate import Severity, validate_build
from tests.fixtures import seed_clean_db as _seed_clean_db


def _insert_daily(db_path: Path, rows: list[tuple[str, float, float, float, float, float]]) -> None:
    conn = get_connection(db_path)
    conn.executemany(
        "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _issues(db_path: Path, *, name: str | None = None, severity: Severity | None = None):
    return [
        issue for issue in validate_build(db_path)
        if (name is None or issue.name == name) and (severity is None or issue.severity == severity)
    ]


class TestEmptyDB:
    def test_empty_computed_daily_returns_no_fatals(self, empty_db: Path) -> None:
        assert _issues(empty_db, severity=Severity.FATAL) == []

    def test_single_day_returns_no_fatals(self, empty_db: Path) -> None:
        _insert_daily(empty_db, [("2025-01-02", 100000, 55000, 15000, 3000, 27000)])
        conn = get_connection(empty_db)
        conn.execute(
            "INSERT INTO computed_daily_tickers (date, ticker, value, category) VALUES ('2025-01-02', 'VOO', 100000, 'US Equity')"
        )
        conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES ('VOO', '2025-01-02', 100.0)")
        conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES ('CNY=X', '2025-01-02', 7.25)")
        conn.commit()
        conn.close()
        assert _issues(empty_db, severity=Severity.FATAL) == []


class TestDayOverDayEdgeCases:
    @pytest.mark.parametrize(
        ("rows", "expected_fatals", "expected_warnings"),
        [
            ([("2025-01-02", 0, 0, 0, 0, 0), ("2025-01-03", 100000, 55000, 15000, 3000, 27000)], 0, 0),
            (
                [
                    ("2025-01-02", 100000, 55000, 15000, 3000, 27000),
                    ("2025-01-03", 110000, 60000, 16500, 3300, 30200),
                ],
                0,
                0,
            ),
            ([("2025-01-02", 5000, 2750, 750, 150, 1350), ("2025-01-03", 5600, 3080, 840, 168, 1512)], 0, 0),
            (
                [
                    ("2025-01-02", 100000, 55000, 15000, 3000, 27000),
                    ("2025-01-03", 125000, 68750, 18750, 3750, 33750),
                ],
                1,
                0,
            ),
            ([("2025-01-02", 50000, 27500, 7500, 1500, 13500), ("2025-01-03", 58000, 31900, 8700, 1740, 15660)], 0, 1),
        ],
        ids=[
            "zero-prev-total",
            "ten-pct-not-flagged",
            "small-portfolio-dollar-gate",
            "large-pct-and-dollar-fatal",
            "moderate-pct-warning",
        ],
    )
    def test_day_over_day_thresholds(
        self,
        empty_db: Path,
        rows: list[tuple[str, float, float, float, float, float]],
        expected_fatals: int,
        expected_warnings: int,
    ) -> None:
        _insert_daily(empty_db, rows)
        day_over_day = _issues(empty_db, name="day_over_day")
        assert len([i for i in day_over_day if i.severity == Severity.FATAL]) == expected_fatals
        assert len([i for i in day_over_day if i.severity == Severity.WARNING]) == expected_warnings

    def test_old_anomaly_suppressed(self, empty_db: Path) -> None:
        """Anomalies older than 7 days before latest_date are not reported
        (not actionable — historical data is immutable past the refresh window)."""
        _insert_daily(empty_db, [
            ("2025-01-02", 50000, 27500, 7500, 1500, 13500),
            ("2025-01-03", 125000, 68750, 18750, 3750, 33750),
            ("2025-02-10", 125050, 68775, 18755, 3751, 33769),
        ])
        assert _issues(empty_db, name="day_over_day") == []


class TestTotalVsTickersTolerance:
    def test_small_diff_within_tolerance(self, empty_db: Path) -> None:
        """Diff <= $1 should be tolerated."""
        _seed_clean_db(empty_db)
        conn = get_connection(empty_db)
        # Make total differ by $0.99 from tickers
        conn.execute("UPDATE computed_daily SET total = 100000.99 WHERE date = '2025-01-02'")
        conn.commit()
        conn.close()
        assert _issues(empty_db, name="total_vs_tickers") == []


class TestDateGapEdgeCases:
    def test_exactly_7_days_not_flagged(self, empty_db: Path) -> None:
        """7-day gap should NOT trigger (only > 7)."""
        _insert_daily(empty_db, [
            ("2025-01-02", 100000, 55000, 15000, 3000, 27000),
            ("2025-01-09", 100500, 55000, 15000, 3000, 27500),
        ])
        assert _issues(empty_db, name="date_gaps") == []


class TestHoldingsEdgeCases:
    def test_small_holding_not_checked(self, empty_db: Path) -> None:
        """Holdings <= $100 should not require price data."""
        conn = get_connection(empty_db)
        conn.execute(
            "INSERT INTO computed_daily_tickers (date, ticker, value, category) VALUES ('2025-01-02', 'PENNY', 50.0, 'US Equity')"
        )
        conn.commit()
        conn.close()
        assert _issues(empty_db, name="holdings_have_prices") == []
