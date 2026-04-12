"""Tests for the consolidated US-date parser in etl.parsing.

This module is the single entry point for converting Fidelity's
``MM/DD/YYYY`` and Robinhood's ``M/D/YYYY`` inputs to ISO ``YYYY-MM-DD``.
"""

from __future__ import annotations

import pytest

from etl.parsing import parse_us_date


class TestParseUsDateStrict:
    """strict=True: demand two-digit month and day (Fidelity's format)."""

    def test_happy_path(self) -> None:
        assert parse_us_date("01/15/2026", strict=True) == "2026-01-15"

    def test_end_of_year(self) -> None:
        assert parse_us_date("12/31/2025", strict=True) == "2025-12-31"

    def test_preserves_leading_zeros(self) -> None:
        assert parse_us_date("09/04/2026", strict=True) == "2026-09-04"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            parse_us_date("", strict=True)

    def test_rejects_single_digit_month(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            parse_us_date("1/15/2026", strict=True)

    def test_rejects_iso_input(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            parse_us_date("2026-01-15", strict=True)

    def test_rejects_garbage(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            parse_us_date("not a date", strict=True)

    def test_error_includes_row_context(self) -> None:
        with pytest.raises(ValueError, match=r"Accounts_History\.csv row 42"):
            parse_us_date("bad", strict=True, row_context="Accounts_History.csv row 42")


class TestParseUsDateLoose:
    """strict=False: accept single-digit month/day (Robinhood's format)."""

    def test_happy_path_zero_padded(self) -> None:
        assert parse_us_date("01/15/2026") == "2026-01-15"

    def test_single_digit_month(self) -> None:
        assert parse_us_date("1/15/2026") == "2026-01-15"

    def test_single_digit_day(self) -> None:
        assert parse_us_date("11/3/2025") == "2025-11-03"

    def test_single_digit_both(self) -> None:
        assert parse_us_date("3/4/2025") == "2025-03-04"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            parse_us_date("", strict=False)

    def test_rejects_iso_input(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            parse_us_date("2026-01-15", strict=False)

    def test_rejects_garbage(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            parse_us_date("not a date", strict=False)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
