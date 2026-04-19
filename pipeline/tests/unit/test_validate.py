"""Tests for post-build validation gate."""
from __future__ import annotations

from pathlib import Path

from etl.db import get_connection
from etl.validate import Severity, validate_build
from tests.fixtures import seed_clean_db as _seed_clean_db

# ── Tests ───────────────────────────────────────────────────────────────────


class TestCleanData:
    def test_passes_all_checks(self, empty_db: Path) -> None:
        _seed_clean_db(empty_db)
        issues = validate_build(empty_db)
        fatals = [i for i in issues if i.severity == Severity.FATAL]
        assert fatals == [], f"Unexpected fatals: {fatals}"


class TestTotalVsTickers:
    def test_mismatch_is_fatal(self, empty_db: Path) -> None:
        _seed_clean_db(empty_db)

        # Break the total so it diverges from ticker sum
        conn = get_connection(empty_db)
        conn.execute("UPDATE computed_daily SET total = 999999 WHERE date = '2025-01-03'")
        conn.commit()
        conn.close()

        issues = validate_build(empty_db)
        fatals = [i for i in issues if i.severity == Severity.FATAL and i.name == "total_vs_tickers"]
        assert len(fatals) >= 1
        assert "2025-01-03" in fatals[0].message


class TestDayOverDay:
    def test_large_change_is_fatal(self, empty_db: Path) -> None:
        _seed_clean_db(empty_db)

        # Spike one day to trigger > 10% change
        conn = get_connection(empty_db)
        conn.execute("UPDATE computed_daily SET total = 200000 WHERE date = '2025-01-03'")
        # Also fix tickers to avoid total_vs_tickers false positive
        conn.execute("UPDATE computed_daily_tickers SET value = 200000 WHERE date = '2025-01-03' AND ticker = 'VOO'")
        conn.commit()
        conn.close()

        issues = validate_build(empty_db)
        fatals = [i for i in issues if i.severity == Severity.FATAL and i.name == "day_over_day"]
        assert len(fatals) >= 1


class TestHoldingsHavePrices:
    def test_missing_price_is_fatal(self, empty_db: Path) -> None:
        _seed_clean_db(empty_db)

        # Remove price data for VOO (which has value > 100)
        conn = get_connection(empty_db)
        conn.execute("DELETE FROM daily_close WHERE symbol = 'VOO'")
        conn.commit()
        conn.close()

        issues = validate_build(empty_db)
        fatals = [i for i in issues if i.severity == Severity.FATAL and i.name == "holdings_have_prices"]
        assert len(fatals) == 1
        assert "VOO" in fatals[0].message


class TestCnyRateFreshness:
    def test_stale_rate_is_warning(self, empty_db: Path) -> None:
        _seed_clean_db(empty_db)

        # Make CNY rate 30 days old relative to latest computed date
        conn = get_connection(empty_db)
        conn.execute("UPDATE daily_close SET date = '2024-12-01' WHERE symbol = 'CNY=X'")
        conn.commit()
        conn.close()

        issues = validate_build(empty_db)
        warnings = [i for i in issues if i.severity == Severity.WARNING and i.name == "cny_rate_freshness"]
        assert len(warnings) == 1
        assert "CNY=X" in warnings[0].message


class TestHoldingsPricesAreFresh:
    """Regression: bug #156 class — forward-fill masks stale prices when fetch skips."""

    def test_stale_price_for_held_symbol_is_fatal(self, empty_db: Path) -> None:
        _seed_clean_db(empty_db)

        # VOO last has a price dated 2024-11-01, but latest computed_daily is
        # 2025-01-06 — 66-day gap (>4). Should surface as FATAL, matching the
        # exact staleness pattern that silently produced wrong totals in #156.
        conn = get_connection(empty_db)
        conn.execute("DELETE FROM daily_close WHERE symbol = 'VOO'")
        conn.execute(
            "INSERT INTO daily_close (symbol, date, close) VALUES ('VOO', '2024-11-01', 100.0)"
        )
        conn.commit()
        conn.close()

        issues = validate_build(empty_db)
        fatals = [i for i in issues if i.severity == Severity.FATAL and i.name == "holdings_prices_are_fresh"]
        assert len(fatals) == 1
        assert "VOO" in fatals[0].message

    def test_weekend_gap_does_not_fire(self, empty_db: Path) -> None:
        """Fri→Mon spans 3 days — within the 4-day tolerance."""
        conn = get_connection(empty_db)
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

        issues = validate_build(empty_db)
        fatals = [i for i in issues if i.severity == Severity.FATAL and i.name == "holdings_prices_are_fresh"]
        assert fatals == []

    def test_book_value_tickers_are_excluded(self, empty_db: Path) -> None:
        """`Cash`, `SPAXX`, `401k sp500` etc. are valued without daily_close — skip them."""
        conn = get_connection(empty_db)
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

        issues = validate_build(empty_db)
        assert [i for i in issues if i.name == "holdings_prices_are_fresh"] == []


class TestCostBasisNonneg:
    """Cost basis is $ paid — always >= 0 (or NULL for legacy/book-value rows)."""

    def test_negative_cost_basis_is_fatal(self, empty_db: Path) -> None:
        _seed_clean_db(empty_db)
        conn = get_connection(empty_db)
        conn.execute(
            "UPDATE computed_daily_tickers SET cost_basis = -100 "
            "WHERE date = '2025-01-06' AND ticker = 'VOO'",
        )
        conn.commit()
        conn.close()

        issues = validate_build(empty_db)
        fatals = [i for i in issues if i.name == "cost_basis_nonneg"]
        assert len(fatals) == 1
        assert "VOO" in fatals[0].message

    def test_zero_cost_basis_is_allowed(self, empty_db: Path) -> None:
        """Zero is a legitimate value (e.g. gifted shares, fully-depreciated lot)."""
        _seed_clean_db(empty_db)
        conn = get_connection(empty_db)
        conn.execute(
            "UPDATE computed_daily_tickers SET cost_basis = 0 "
            "WHERE date = '2025-01-06' AND ticker = 'VOO'",
        )
        conn.commit()
        conn.close()
        issues = validate_build(empty_db)
        assert [i for i in issues if i.name == "cost_basis_nonneg"] == []


class TestCategorySubtypeEnums:
    """Unknown category / subtype values must surface — not silently render wrong."""

    def test_unknown_category_is_fatal(self, empty_db: Path) -> None:
        _seed_clean_db(empty_db)
        conn = get_connection(empty_db)
        conn.execute(
            "UPDATE computed_daily_tickers SET category = 'Bogus' "
            "WHERE date = '2025-01-06' AND ticker = 'VOO'",
        )
        conn.commit()
        conn.close()

        issues = validate_build(empty_db)
        fatals = [i for i in issues if i.name == "category_enum"]
        assert len(fatals) == 1
        assert "Bogus" in fatals[0].message

    def test_unknown_subtype_is_fatal(self, empty_db: Path) -> None:
        _seed_clean_db(empty_db)
        conn = get_connection(empty_db)
        conn.execute(
            "UPDATE computed_daily_tickers SET subtype = 'zzz-unknown' "
            "WHERE date = '2025-01-06' AND ticker = 'VOO'",
        )
        conn.commit()
        conn.close()

        issues = validate_build(empty_db)
        fatals = [i for i in issues if i.name == "subtype_enum"]
        assert len(fatals) == 1
        assert "zzz-unknown" in fatals[0].message

    def test_liability_category_is_known(self, empty_db: Path) -> None:
        """Liability was missing from an early draft; now properly allowed."""
        _seed_clean_db(empty_db)
        conn = get_connection(empty_db)
        conn.execute(
            "UPDATE computed_daily_tickers SET category = 'Liability' "
            "WHERE date = '2025-01-06' AND ticker = 'VOO'",
        )
        conn.commit()
        conn.close()
        issues = validate_build(empty_db)
        assert [i for i in issues if i.name == "category_enum"] == []


class TestDateGaps:
    def test_large_gap_is_warning(self, empty_db: Path) -> None:
        conn = get_connection(empty_db)
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

        issues = validate_build(empty_db)
        warnings = [i for i in issues if i.severity == Severity.WARNING and i.name == "date_gaps"]
        assert len(warnings) == 1
        assert "10-day gap" in warnings[0].message
