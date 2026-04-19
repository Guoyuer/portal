"""Extended tests for validate.py edge cases."""
from __future__ import annotations

from pathlib import Path

from etl.db import get_connection
from etl.validate import Severity, validate_build


def _seed_clean_db(db_path: Path) -> None:
    """Minimal DB that passes all checks. Pre: DB already schema-initialized."""
    conn = get_connection(db_path)
    for dt, total in [("2025-01-02", 100000), ("2025-01-03", 100500), ("2025-01-06", 101000)]:
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES (?, ?, 55000, 15000, 3000, 27000)", (dt, total),
        )
        conn.execute(
            "INSERT INTO computed_daily_tickers (date, ticker, value, category) VALUES (?, 'VOO', ?, 'US Equity')",
            (dt, total),
        )
    for sym in ("VOO",):
        conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES (?, '2025-01-06', 100.0)", (sym,))
    conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES ('CNY=X', '2025-01-06', 7.25)")
    conn.commit()
    conn.close()


class TestEmptyDB:
    def test_empty_computed_daily_returns_no_fatals(self, empty_db: Path) -> None:
        issues = validate_build(empty_db)
        fatals = [i for i in issues if i.severity == Severity.FATAL]
        assert fatals == []

    def test_single_day_returns_no_fatals(self, empty_db: Path) -> None:
        conn = get_connection(empty_db)
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-02', 100000, 55000, 15000, 3000, 27000)"
        )
        conn.execute(
            "INSERT INTO computed_daily_tickers (date, ticker, value, category) VALUES ('2025-01-02', 'VOO', 100000, 'US Equity')"
        )
        conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES ('VOO', '2025-01-02', 100.0)")
        conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES ('CNY=X', '2025-01-02', 7.25)")
        conn.commit()
        conn.close()
        issues = validate_build(empty_db)
        fatals = [i for i in issues if i.severity == Severity.FATAL]
        assert fatals == []


class TestDayOverDayEdgeCases:
    def test_zero_prev_total_skipped(self, empty_db: Path) -> None:
        """Day-over-day check should skip when previous total is 0."""
        conn = get_connection(empty_db)
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-02', 0, 0, 0, 0, 0)"
        )
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-03', 100000, 55000, 15000, 3000, 27000)"
        )
        conn.commit()
        conn.close()
        issues = validate_build(empty_db)
        day_over_day = [i for i in issues if i.name == "day_over_day"]
        assert day_over_day == []

    def test_exactly_10_pct_not_flagged(self, empty_db: Path) -> None:
        """10% on a large portfolio should NOT trigger (below 15% threshold)."""
        conn = get_connection(empty_db)
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-02', 100000, 55000, 15000, 3000, 27000)"
        )
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-03', 110000, 60000, 16500, 3300, 30200)"
        )
        conn.commit()
        conn.close()
        issues = validate_build(empty_db)
        day_over_day = [i for i in issues if i.name == "day_over_day"]
        assert day_over_day == []

    def test_moderate_change_small_portfolio_not_flagged(self, empty_db: Path) -> None:
        """12% change on a small portfolio ($600 absolute) should not be flagged."""
        conn = get_connection(empty_db)
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-02', 5000, 2750, 750, 150, 1350)"
        )
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-03', 5600, 3080, 840, 168, 1512)"
        )
        conn.commit()
        conn.close()
        issues = validate_build(empty_db)
        day_over_day = [i for i in issues if i.name == "day_over_day"]
        assert day_over_day == []

    def test_large_pct_large_dollar_is_fatal(self, empty_db: Path) -> None:
        """25% change with >$10k absolute should be FATAL."""
        conn = get_connection(empty_db)
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-02', 100000, 55000, 15000, 3000, 27000)"
        )
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-03', 125000, 68750, 18750, 3750, 33750)"
        )
        conn.commit()
        conn.close()
        issues = validate_build(empty_db)
        fatals = [i for i in issues if i.name == "day_over_day" and i.severity == Severity.FATAL]
        assert len(fatals) == 1

    def test_moderate_pct_large_dollar_is_warning(self, empty_db: Path) -> None:
        """16% change with >$5k absolute should be WARNING, not FATAL."""
        conn = get_connection(empty_db)
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-02', 50000, 27500, 7500, 1500, 13500)"
        )
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-03', 58000, 31900, 8700, 1740, 15660)"
        )
        conn.commit()
        conn.close()
        issues = validate_build(empty_db)
        day_over_day = [i for i in issues if i.name == "day_over_day"]
        fatals = [i for i in day_over_day if i.severity == Severity.FATAL]
        warnings = [i for i in day_over_day if i.severity == Severity.WARNING]
        assert len(fatals) == 0
        assert len(warnings) == 1

    def test_old_anomaly_suppressed(self, empty_db: Path) -> None:
        """Anomalies older than 7 days before latest_date are not reported
        (not actionable — historical data is immutable past the refresh window)."""
        conn = get_connection(empty_db)
        # Jump on 2025-01-03 (old).
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-02', 50000, 27500, 7500, 1500, 13500)"
        )
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-03', 125000, 68750, 18750, 3750, 33750)"  # +150%
        )
        # Latest date far ahead → 2025-01-03 falls outside the 7-day window.
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-02-10', 125050, 68775, 18755, 3751, 33769)"
        )
        conn.commit()
        conn.close()
        issues = validate_build(empty_db)
        day_over_day = [i for i in issues if i.name == "day_over_day"]
        assert day_over_day == []


class TestTotalVsTickersTolerance:
    def test_small_diff_within_tolerance(self, empty_db: Path) -> None:
        """Diff <= $1 should be tolerated."""
        _seed_clean_db(empty_db)
        conn = get_connection(empty_db)
        # Make total differ by $0.99 from tickers
        conn.execute("UPDATE computed_daily SET total = 100000.99 WHERE date = '2025-01-02'")
        conn.commit()
        conn.close()
        issues = validate_build(empty_db)
        mismatches = [i for i in issues if i.name == "total_vs_tickers"]
        assert mismatches == []


class TestDateGapEdgeCases:
    def test_exactly_7_days_not_flagged(self, empty_db: Path) -> None:
        """7-day gap should NOT trigger (only > 7)."""
        conn = get_connection(empty_db)
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-02', 100000, 55000, 15000, 3000, 27000)"
        )
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-09', 100500, 55000, 15000, 3000, 27500)"
        )
        conn.commit()
        conn.close()
        issues = validate_build(empty_db)
        gaps = [i for i in issues if i.name == "date_gaps"]
        assert gaps == []


class TestHoldingsEdgeCases:
    def test_small_holding_not_checked(self, empty_db: Path) -> None:
        """Holdings <= $100 should not require price data."""
        conn = get_connection(empty_db)
        conn.execute(
            "INSERT INTO computed_daily_tickers (date, ticker, value, category) VALUES ('2025-01-02', 'PENNY', 50.0, 'US Equity')"
        )
        conn.commit()
        conn.close()
        issues = validate_build(empty_db)
        holdings = [i for i in issues if i.name == "holdings_have_prices"]
        assert holdings == []
