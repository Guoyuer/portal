"""Tests for post-build validation gate."""
from __future__ import annotations

from pathlib import Path

from etl.db import get_connection, init_db
from etl.validate import Severity, validate_build

# ── Helpers ─────────────────────────────────────────────────────────────────


def _seed_clean_db(db_path: Path) -> None:
    """Populate a minimal DB that passes all validation checks."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        # Three consecutive trading days with stable totals
        for dt, total in [("2025-01-02", 100000), ("2025-01-03", 100500), ("2025-01-06", 101000)]:
            conn.execute(
                "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
                " VALUES (?, ?, 55000, 15000, 3000, 27000)",
                (dt, total),
            )
            # Ticker breakdown that sums to total (value > 0 only)
            conn.execute(
                "INSERT INTO computed_daily_tickers (date, ticker, value, category) VALUES (?, 'VOO', ?, 'US Equity')",
                (dt, total * 0.55),
            )
            conn.execute(
                "INSERT INTO computed_daily_tickers (date, ticker, value, category) VALUES (?, 'VXUS', ?, 'Non-US Equity')",
                (dt, total * 0.15),
            )
            conn.execute(
                "INSERT INTO computed_daily_tickers (date, ticker, value, category) VALUES (?, 'BTC', ?, 'Crypto')",
                (dt, total * 0.03),
            )
            conn.execute(
                "INSERT INTO computed_daily_tickers (date, ticker, value, category) VALUES (?, 'HYSA', ?, 'Safe Net')",
                (dt, total * 0.27),
            )

        # Prices for all tickers with value > 100
        for sym in ("VOO", "VXUS", "BTC", "HYSA"):
            conn.execute(
                "INSERT INTO daily_close (symbol, date, close) VALUES (?, '2025-01-06', 100.0)",
                (sym,),
            )

        # Fresh CNY rate
        conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES ('CNY=X', '2025-01-06', 7.25)")

        conn.commit()
    finally:
        conn.close()


# ── Tests ───────────────────────────────────────────────────────────────────


class TestCleanData:
    def test_passes_all_checks(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _seed_clean_db(db_path)
        issues = validate_build(db_path)
        fatals = [i for i in issues if i.severity == Severity.FATAL]
        assert fatals == [], f"Unexpected fatals: {fatals}"


class TestTotalVsTickers:
    def test_mismatch_is_fatal(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _seed_clean_db(db_path)

        # Break the total so it diverges from ticker sum
        conn = get_connection(db_path)
        conn.execute("UPDATE computed_daily SET total = 999999 WHERE date = '2025-01-03'")
        conn.commit()
        conn.close()

        issues = validate_build(db_path)
        fatals = [i for i in issues if i.severity == Severity.FATAL and i.name == "total_vs_tickers"]
        assert len(fatals) >= 1
        assert "2025-01-03" in fatals[0].message


class TestDayOverDay:
    def test_large_change_is_fatal(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _seed_clean_db(db_path)

        # Spike one day to trigger > 10% change
        conn = get_connection(db_path)
        conn.execute("UPDATE computed_daily SET total = 200000 WHERE date = '2025-01-03'")
        # Also fix tickers to avoid total_vs_tickers false positive
        conn.execute("UPDATE computed_daily_tickers SET value = 200000 WHERE date = '2025-01-03' AND ticker = 'VOO'")
        conn.commit()
        conn.close()

        issues = validate_build(db_path)
        fatals = [i for i in issues if i.severity == Severity.FATAL and i.name == "day_over_day"]
        assert len(fatals) >= 1


class TestHoldingsHavePrices:
    def test_missing_price_is_fatal(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _seed_clean_db(db_path)

        # Remove price data for VOO (which has value > 100)
        conn = get_connection(db_path)
        conn.execute("DELETE FROM daily_close WHERE symbol = 'VOO'")
        conn.commit()
        conn.close()

        issues = validate_build(db_path)
        fatals = [i for i in issues if i.severity == Severity.FATAL and i.name == "holdings_have_prices"]
        assert len(fatals) == 1
        assert "VOO" in fatals[0].message


class TestCnyRateFreshness:
    def test_stale_rate_is_warning(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _seed_clean_db(db_path)

        # Make CNY rate 30 days old relative to latest computed date
        conn = get_connection(db_path)
        conn.execute("UPDATE daily_close SET date = '2024-12-01' WHERE symbol = 'CNY=X'")
        conn.commit()
        conn.close()

        issues = validate_build(db_path)
        warnings = [i for i in issues if i.severity == Severity.WARNING and i.name == "cny_rate_freshness"]
        assert len(warnings) == 1
        assert "CNY=X" in warnings[0].message


class TestHoldingsPricesAreFresh:
    """Regression: bug #156 class — forward-fill masks stale prices when fetch skips."""

    def test_stale_price_for_held_symbol_is_fatal(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _seed_clean_db(db_path)

        # VOO last has a price dated 2024-11-01, but latest computed_daily is
        # 2025-01-06 — 66-day gap (>4). Should surface as FATAL, matching the
        # exact staleness pattern that silently produced wrong totals in #156.
        conn = get_connection(db_path)
        conn.execute("DELETE FROM daily_close WHERE symbol = 'VOO'")
        conn.execute(
            "INSERT INTO daily_close (symbol, date, close) VALUES ('VOO', '2024-11-01', 100.0)"
        )
        conn.commit()
        conn.close()

        issues = validate_build(db_path)
        fatals = [i for i in issues if i.severity == Severity.FATAL and i.name == "holdings_prices_are_fresh"]
        assert len(fatals) == 1
        assert "VOO" in fatals[0].message

    def test_weekend_gap_does_not_fire(self, tmp_path: Path) -> None:
        """Fri→Mon spans 3 days — within the 4-day tolerance."""
        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
        # Monday computed_daily
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-06', 100000, 55000, 15000, 3000, 27000)"
        )
        conn.execute(
            "INSERT INTO computed_daily_tickers (date, ticker, value, category) VALUES ('2025-01-06', 'VOO', 55000, 'US Equity')"
        )
        # VOO's last close is Friday — 3 days behind, tolerable
        conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES ('VOO', '2025-01-03', 100.0)")
        conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES ('CNY=X', '2025-01-06', 7.25)")
        conn.commit()
        conn.close()

        issues = validate_build(db_path)
        fatals = [i for i in issues if i.severity == Severity.FATAL and i.name == "holdings_prices_are_fresh"]
        assert fatals == []

    def test_book_value_tickers_are_excluded(self, tmp_path: Path) -> None:
        """`Cash`, `SPAXX`, `401k sp500` etc. are valued without daily_close — skip them."""
        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-06', 100000, 0, 0, 0, 100000)"
        )
        conn.execute(
            "INSERT INTO computed_daily_tickers (date, ticker, value, category) VALUES ('2025-01-06', 'Cash', 100000, 'Safe Net')"
        )
        conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES ('CNY=X', '2025-01-06', 7.25)")
        conn.commit()
        conn.close()

        issues = validate_build(db_path)
        assert [i for i in issues if i.name == "holdings_prices_are_fresh"] == []


class TestDateGaps:
    def test_large_gap_is_warning(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)

        conn = get_connection(db_path)
        # Two dates 10 days apart
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-02', 100000, 55000, 15000, 3000, 27000)"
        )
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES ('2025-01-12', 101000, 55500, 15200, 3100, 27200)"
        )
        conn.commit()
        conn.close()

        issues = validate_build(db_path)
        warnings = [i for i in issues if i.severity == Severity.WARNING and i.name == "date_gaps"]
        assert len(warnings) == 1
        assert "10-day gap" in warnings[0].message
