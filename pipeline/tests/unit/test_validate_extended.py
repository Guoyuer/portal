"""Extended tests for validate.py edge cases."""
from __future__ import annotations

from pathlib import Path

from generate_asset_snapshot.db import get_connection, init_db
from generate_asset_snapshot.validate import Severity, validate_build


def _seed_clean_db(db_path: Path) -> None:
    """Minimal DB that passes all checks."""
    init_db(db_path)
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
    def test_empty_computed_daily_returns_no_fatals(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        issues = validate_build(db_path)
        fatals = [i for i in issues if i.severity == Severity.FATAL]
        assert fatals == []

    def test_single_day_returns_no_fatals(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
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
        issues = validate_build(db_path)
        fatals = [i for i in issues if i.severity == Severity.FATAL]
        assert fatals == []


class TestDayOverDayEdgeCases:
    def test_zero_prev_total_skipped(self, tmp_path: Path) -> None:
        """Day-over-day check should skip when previous total is 0."""
        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
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
        issues = validate_build(db_path)
        day_over_day = [i for i in issues if i.name == "day_over_day"]
        assert day_over_day == []

    def test_exactly_10_pct_not_flagged(self, tmp_path: Path) -> None:
        """10% exactly should NOT trigger FATAL (only > 10%)."""
        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
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
        issues = validate_build(db_path)
        day_over_day = [i for i in issues if i.name == "day_over_day"]
        assert day_over_day == []


class TestTotalVsTickersTolerance:
    def test_small_diff_within_tolerance(self, tmp_path: Path) -> None:
        """Diff <= $1 should be tolerated."""
        db_path = tmp_path / "test.db"
        _seed_clean_db(db_path)
        conn = get_connection(db_path)
        # Make total differ by $0.99 from tickers
        conn.execute("UPDATE computed_daily SET total = 100000.99 WHERE date = '2025-01-02'")
        conn.commit()
        conn.close()
        issues = validate_build(db_path)
        mismatches = [i for i in issues if i.name == "total_vs_tickers"]
        assert mismatches == []


class TestDateGapEdgeCases:
    def test_exactly_7_days_not_flagged(self, tmp_path: Path) -> None:
        """7-day gap should NOT trigger (only > 7)."""
        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
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
        issues = validate_build(db_path)
        gaps = [i for i in issues if i.name == "date_gaps"]
        assert gaps == []


class TestHoldingsEdgeCases:
    def test_small_holding_not_checked(self, tmp_path: Path) -> None:
        """Holdings <= $100 should not require price data."""
        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
        conn.execute(
            "INSERT INTO computed_daily_tickers (date, ticker, value, category) VALUES ('2025-01-02', 'PENNY', 50.0, 'US Equity')"
        )
        conn.commit()
        conn.close()
        issues = validate_build(db_path)
        holdings = [i for i in issues if i.name == "holdings_have_prices"]
        assert holdings == []
