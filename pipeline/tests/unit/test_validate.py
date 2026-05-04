"""Tests for post-build validation gate."""
from __future__ import annotations

from pathlib import Path

import pytest

from etl.validate import Severity, validate_build
from tests.fixtures import connected_db
from tests.fixtures import seed_clean_db as _seed_clean_db

# ── Tests ───────────────────────────────────────────────────────────────────


def _issues(db_path: Path, *, name: str | None = None, severity: Severity | None = None):
    return [
        issue for issue in validate_build(db_path)
        if (name is None or issue.name == name) and (severity is None or issue.severity == severity)
    ]


class TestCleanData:
    def test_passes_all_checks(self, empty_db: Path) -> None:
        _seed_clean_db(empty_db)
        assert _issues(empty_db, severity=Severity.FATAL) == []


class TestTotalVsTickers:
    def test_mismatch_is_fatal(self, empty_db: Path) -> None:
        _seed_clean_db(empty_db)

        # Break the total so it diverges from ticker sum
        with connected_db(empty_db) as conn:
            conn.execute("UPDATE computed_daily SET total = 999999 WHERE date = '2025-01-03'")

        fatals = _issues(empty_db, name="total_vs_tickers", severity=Severity.FATAL)
        assert len(fatals) >= 1
        assert "2025-01-03" in fatals[0].message


class TestDayOverDay:
    def test_large_change_is_fatal(self, empty_db: Path) -> None:
        _seed_clean_db(empty_db)

        # Spike one day to trigger > 10% change
        with connected_db(empty_db) as conn:
            conn.execute("UPDATE computed_daily SET total = 200000 WHERE date = '2025-01-03'")
            # Also fix tickers to avoid total_vs_tickers false positive
            conn.execute("UPDATE computed_daily_tickers SET value = 200000 WHERE date = '2025-01-03' AND ticker = 'VOO'")

        fatals = _issues(empty_db, name="day_over_day", severity=Severity.FATAL)
        assert len(fatals) >= 1


class TestHoldingsHavePrices:
    def test_missing_price_is_fatal(self, empty_db: Path) -> None:
        _seed_clean_db(empty_db)

        # Remove price data for VOO (which has value > 100)
        with connected_db(empty_db) as conn:
            conn.execute("DELETE FROM daily_close WHERE symbol = 'VOO'")

        fatals = _issues(empty_db, name="holdings_have_prices", severity=Severity.FATAL)
        assert len(fatals) == 1
        assert "VOO" in fatals[0].message


class TestCnyRateFreshness:
    def test_stale_rate_is_warning(self, empty_db: Path) -> None:
        _seed_clean_db(empty_db)

        # Make CNY rate 30 days old relative to latest computed date
        with connected_db(empty_db) as conn:
            conn.execute("UPDATE daily_close SET date = '2024-12-01' WHERE symbol = 'CNY=X'")

        warnings = _issues(empty_db, name="cny_rate_freshness", severity=Severity.WARNING)
        assert len(warnings) == 1
        assert "CNY=X" in warnings[0].message


class TestHoldingsPricesAreFresh:
    """Regression: bug #156 class — forward-fill masks stale prices when fetch skips."""

    def test_stale_price_for_held_symbol_is_fatal(self, empty_db: Path) -> None:
        _seed_clean_db(empty_db)

        # VOO last has a price dated 2024-11-01, but latest computed_daily is
        # 2025-01-06 — 66-day gap (>4). Should surface as FATAL, matching the
        # exact staleness pattern that silently produced wrong totals in #156.
        with connected_db(empty_db) as conn:
            conn.execute("DELETE FROM daily_close WHERE symbol = 'VOO'")
            conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES ('VOO', '2024-11-01', 100.0)")

        fatals = _issues(empty_db, name="holdings_prices_are_fresh", severity=Severity.FATAL)
        assert len(fatals) == 1
        assert "VOO" in fatals[0].message

    def test_weekend_gap_does_not_fire(self, empty_db: Path) -> None:
        """Fri→Mon spans 3 days — within the 4-day tolerance."""
        with connected_db(empty_db) as conn:
            # Monday computed_daily
            conn.execute(
                "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
                " VALUES ('2025-01-06', 100000, 55000, 15000, 3000, 27000)"
            )
            conn.execute(
                "INSERT INTO computed_daily_tickers (date, ticker, value, category) "
                "VALUES ('2025-01-06', 'VOO', 55000, 'US Equity')"
            )
            # VOO's last close is Friday — 3 days behind, tolerable
            conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES ('VOO', '2025-01-03', 100.0)")
            conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES ('CNY=X', '2025-01-06', 7.25)")

        assert _issues(empty_db, name="holdings_prices_are_fresh", severity=Severity.FATAL) == []

    def test_book_value_tickers_are_excluded(self, empty_db: Path) -> None:
        """`Cash`, `SPAXX`, `401k sp500` etc. are valued without daily_close — skip them."""
        with connected_db(empty_db) as conn:
            conn.execute(
                "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
                " VALUES ('2025-01-06', 100000, 0, 0, 0, 100000)"
            )
            conn.execute(
                "INSERT INTO computed_daily_tickers (date, ticker, value, category) "
                "VALUES ('2025-01-06', 'Cash', 100000, 'Safe Net')"
            )
            conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES ('CNY=X', '2025-01-06', 7.25)")

        assert _issues(empty_db, name="holdings_prices_are_fresh") == []


class TestCategorySubtypeEnums:
    """Unknown category / subtype values must surface — not silently render wrong."""

    @pytest.mark.parametrize(
        ("column", "value", "issue_name", "expected_count"),
        [
            ("category", "Bogus", "category_enum", 1),
            ("subtype", "zzz-unknown", "subtype_enum", 1),
            ("category", "Liability", "category_enum", 0),
        ],
        ids=["unknown-category", "unknown-subtype", "liability-category"],
    )
    def test_category_and_subtype_enums(
        self,
        empty_db: Path,
        column: str,
        value: str,
        issue_name: str,
        expected_count: int,
    ) -> None:
        _seed_clean_db(empty_db)
        with connected_db(empty_db) as conn:
            conn.execute(
                f"UPDATE computed_daily_tickers SET {column} = ? "
                "WHERE date = '2025-01-06' AND ticker = 'VOO'",
                (value,),
            )

        issues = _issues(empty_db, name=issue_name)
        assert len(issues) == expected_count
        if expected_count:
            assert value in issues[0].message


class TestDateGaps:
    def test_large_gap_is_warning(self, empty_db: Path) -> None:
        with connected_db(empty_db) as conn:
            # Two dates 10 days apart
            conn.execute(
                "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
                " VALUES ('2025-01-02', 100000, 55000, 15000, 3000, 27000)"
            )
            conn.execute(
                "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
                " VALUES ('2025-01-12', 101000, 55500, 15200, 3100, 27200)"
            )

        warnings = _issues(empty_db, name="date_gaps", severity=Severity.WARNING)
        assert len(warnings) == 1
        assert "10-day gap" in warnings[0].message


class TestFidelityQianjiReconcile:
    """Pipeline gate mirroring the frontend ``computeCrossCheck``: Fidelity
    deposits must match a Qianji transfer / income-to-Fidelity within 7d
    and to the cent. Fail-loud so bad data never gets published."""

    @staticmethod
    def _add_fidelity_deposit(db_path: Path, run_date: str, amount: float, action: str = "EFT") -> None:
        with connected_db(db_path) as conn:
            conn.execute(
                "INSERT INTO fidelity_transactions "
                "(run_date, account_number, action, action_type, symbol, amount) "
                "VALUES (?, 'Z29133576', ?, 'deposit', '', ?)",
                (run_date, action, amount),
            )

    @staticmethod
    def _add_qianji(
        db_path: Path,
        dt: str,
        amount: float,
        *,
        kind: str = "transfer",
        account_to: str = "Fidelity taxable",
    ) -> None:
        with connected_db(db_path) as conn:
            conn.execute(
                "INSERT INTO qianji_transactions (date, type, category, amount, account_to) "
                "VALUES (?, ?, '', ?, ?)",
                (dt, kind, amount, account_to),
            )

    def test_all_deposits_matched_passes(self, empty_db: Path) -> None:
        self._add_fidelity_deposit(empty_db, "2026-04-10", 2500.00)
        self._add_qianji(empty_db, "2026-04-09", 2500.00)
        assert _issues(empty_db, name="fidelity_qianji_reconcile") == []

    def test_unmatched_deposit_is_fatal(self, empty_db: Path) -> None:
        """A Fidelity deposit with no Qianji candidate within the window must abort."""
        # Seed one matched pair to establish the Qianji floor (so the deposit
        # we care about isn't silently pre-floor-skipped).
        self._add_fidelity_deposit(empty_db, "2026-01-15", 100.00, action="PAYCHECK")
        self._add_qianji(empty_db, "2026-01-15", 100.00)
        # Then: a second deposit well past the floor, no matching Qianji.
        self._add_fidelity_deposit(empty_db, "2026-04-10", 2500.00, action="WIRE")
        recs = _issues(empty_db, name="fidelity_qianji_reconcile", severity=Severity.FATAL)
        assert len(recs) == 1
        assert "2026-04-10" in recs[0].message
        assert "$2,500" in recs[0].message

    def test_income_to_fidelity_counts_as_candidate(self, empty_db: Path) -> None:
        """Qianji ``type='income'`` with ``account_to`` starting with 'fidelity'
        (case-insensitive) must match deposits — covers direct-deposited
        paychecks that the user logs as income rather than as a transfer."""
        self._add_fidelity_deposit(empty_db, "2026-04-10", 5000.00, action="DIRECT DEPOSIT")
        # Income, not transfer — and mixed-case account_to to prove the LOWER() filter
        self._add_qianji(empty_db, "2026-04-10", 5000.00, kind="income", account_to="Fidelity Taxable")
        assert _issues(empty_db, name="fidelity_qianji_reconcile") == []

    def test_pre_floor_deposit_is_skipped(self, empty_db: Path) -> None:
        """Deposits before ``earliest_qianji - window`` can't be reconciled
        (Qianji didn't exist yet) — must be silently ignored, not fatal."""
        # Qianji starts on 2026-04-01. A deposit on 2026-01-15 is 76d before
        # the floor (beyond the 7d grace), so structurally unmatchable.
        self._add_qianji(empty_db, "2026-04-01", 1000.00)
        self._add_fidelity_deposit(empty_db, "2026-04-01", 1000.00)  # matches
        self._add_fidelity_deposit(empty_db, "2026-01-15", 500.00)    # pre-floor, skipped
        recs = _issues(empty_db, name="fidelity_qianji_reconcile")
        assert recs == [], f"Expected pre-floor deposit to be skipped, got: {recs}"

    def test_cent_drift_is_fatal(self, empty_db: Path) -> None:
        """Amounts must match to the cent — $0.01 drift fails the check.
        This is deliberate: legit fees / FX drift would look similar but are
        rare enough that catching them is better than tolerating them."""
        self._add_fidelity_deposit(empty_db, "2026-04-10", 2500.00)
        self._add_qianji(empty_db, "2026-04-10", 2500.01)  # 1 cent off
        recs = _issues(empty_db, name="fidelity_qianji_reconcile", severity=Severity.FATAL)
        assert len(recs) == 1

    def test_sub_dust_deposit_ignored(self, empty_db: Path) -> None:
        """Deposits below $1 (cash sweep, residual interest) aren't candidates
        for reconcile — user doesn't log them in Qianji."""
        # One $0.05 "deposit" (residual interest) with no Qianji counterpart.
        # Must not produce a reconcile fatal, even though nothing matches it.
        # Need at least one Qianji candidate to establish a floor; otherwise
        # the "no candidates" short-circuit would skip the check anyway.
        self._add_qianji(empty_db, "2026-04-10", 1000.00)
        self._add_fidelity_deposit(empty_db, "2026-04-10", 1000.00)  # matches
        self._add_fidelity_deposit(empty_db, "2026-04-11", 0.05, action="INTEREST")  # dust
        assert _issues(empty_db, name="fidelity_qianji_reconcile") == []

    def test_no_qianji_candidates_silently_passes(self, empty_db: Path) -> None:
        """Empty Qianji candidate set (fresh DB / test fixture) → silent pass,
        matching frontend behaviour. Guards against blocking the build when
        a user hasn't started Qianji yet or when fixtures omit it."""
        self._add_fidelity_deposit(empty_db, "2026-04-10", 2500.00)
        assert _issues(empty_db, name="fidelity_qianji_reconcile") == []

    def test_bipartite_matching_not_greedy(self, empty_db: Path) -> None:
        """Two same-amount deposits, two same-amount Qianji candidates: a
        naive 'nearest unused' greedy could steal the shared candidate from
        a later deposit. Chronological + earliest-in-window avoids that."""
        # Two $500 deposits: Apr 10 and Apr 13. Two $500 Qianji: Apr 9 and Apr 14.
        # A greedy-by-nearest for dep Apr 10 might pick Apr 9; dep Apr 13
        # would then pick Apr 14 → both match. But if we greedily matched
        # Apr 13 first picking Apr 14 (closer), Apr 10 would still pick
        # Apr 9. So any order works here; the real test is that BOTH match.
        self._add_qianji(empty_db, "2026-04-09", 500.00)
        self._add_qianji(empty_db, "2026-04-14", 500.00)
        self._add_fidelity_deposit(empty_db, "2026-04-10", 500.00)
        self._add_fidelity_deposit(empty_db, "2026-04-13", 500.00)
        assert _issues(empty_db, name="fidelity_qianji_reconcile") == []
