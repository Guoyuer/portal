"""Unit tests for the internal precompute helpers.

End-to-end coverage of ``precompute_market`` lives in
``test_precompute_market.py`` — this file isolates the smaller building blocks
(``_compute_index_row``, ``_precompute_indices``, ``_precompute_cny``,
``_precompute_fred``) so their failure modes pin cleanly.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from etl.db import get_connection
from etl.precompute import (
    _compute_index_row,
    _precompute_cny,
    _precompute_fred,
    _precompute_indices,
)


@pytest.fixture(autouse=True)
def _no_dxy_network():
    """Stub fetch_dxy_monthly so any helper that transitively calls it is
    offline-safe (some share an import path with precompute_market)."""
    with patch("etl.market.yahoo.fetch_dxy_monthly", return_value=[]):
        yield


# ── Helpers ─────────────────────────────────────────────────────────────────


def _seed_prices(db_path: Path, ticker: str, prices: list[tuple[str, float]]) -> None:
    conn = get_connection(db_path)
    for dt, close in prices:
        conn.execute(
            "INSERT OR REPLACE INTO daily_close (symbol, date, close) VALUES (?, ?, ?)",
            (ticker, dt, close),
        )
    conn.commit()
    conn.close()


# ── _compute_index_row ──────────────────────────────────────────────────────


class TestComputeIndexRow:
    def test_returns_none_for_insufficient_data(self) -> None:
        rows = [("2025-01-02", 5000.0)]
        assert _compute_index_row("^GSPC", "S&P 500", rows) is None

    def test_returns_none_for_missing_ticker(self) -> None:
        assert _compute_index_row("MISSING", "Missing Index", []) is None

    def test_computes_returns_for_valid_data(self) -> None:
        # 30 trading days of steadily rising prices
        prices = [(f"2025-01-{d:02d}", 5000.0 + d * 10) for d in range(2, 32)]
        row = _compute_index_row("^GSPC", "S&P 500", prices)
        assert row is not None
        assert row["ticker"] == "^GSPC"
        assert row["name"] == "S&P 500"
        assert row["current"] == prices[-1][1]
        assert row["high_52w"] >= row["low_52w"]
        assert isinstance(row["month_return"], float)
        assert isinstance(row["ytd_return"], float)


# ── _precompute_indices ─────────────────────────────────────────────────────


class TestPrecomputeIndices:
    def test_inserts_rows_for_seeded_indices(self, empty_db: Path) -> None:
        prices = [(f"2025-01-{d:02d}", 5000.0 + d) for d in range(2, 28)]
        _seed_prices(empty_db, "^GSPC", prices)

        conn = get_connection(empty_db)
        try:
            _precompute_indices(conn)
            conn.commit()
            rows = conn.execute("SELECT ticker FROM computed_market_indices").fetchall()
        finally:
            conn.close()
        assert ("^GSPC",) in rows

    def test_noop_for_unseeded_indices(self, empty_db: Path) -> None:
        conn = get_connection(empty_db)
        try:
            _precompute_indices(conn)
            conn.commit()
            rows = conn.execute("SELECT * FROM computed_market_indices").fetchall()
        finally:
            conn.close()
        assert rows == []


# ── _precompute_cny ─────────────────────────────────────────────────────────


class TestPrecomputeCny:
    def test_writes_monthly_last_close_to_econ_series(self, empty_db: Path) -> None:
        _seed_prices(
            empty_db,
            "CNY=X",
            [("2025-01-15", 7.20), ("2025-01-31", 7.30), ("2025-02-28", 7.25)],
        )

        conn = get_connection(empty_db)
        try:
            _precompute_cny(conn)
            conn.commit()
            rows = conn.execute(
                "SELECT date, value FROM econ_series WHERE key='usdCny' ORDER BY date"
            ).fetchall()
        finally:
            conn.close()
        # Last close per YYYY-MM bucket
        assert rows == [("2025-01", 7.30), ("2025-02", 7.25)]

    def test_noop_without_cny_data(self, empty_db: Path) -> None:
        conn = get_connection(empty_db)
        try:
            _precompute_cny(conn)  # should not raise
            conn.commit()
            rows = conn.execute("SELECT * FROM econ_series").fetchall()
        finally:
            conn.close()
        assert rows == []


# ── _precompute_fred ────────────────────────────────────────────────────────


class TestPrecomputeFred:
    def test_noop_without_api_key(self, empty_db: Path, monkeypatch) -> None:
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        conn = get_connection(empty_db)
        try:
            _precompute_fred(conn)  # should not raise
            conn.commit()
            rows = conn.execute("SELECT * FROM econ_series").fetchall()
        finally:
            conn.close()
        assert rows == []

    def test_inserts_series_when_api_returns_data(
        self, empty_db: Path, monkeypatch
    ) -> None:
        """Series rows land in econ_series (1:1, no remapping)."""
        monkeypatch.setenv("FRED_API_KEY", "fake-key")
        fake_fred = {
            "series": {
                "fedRate": [
                    {"date": "2025-01-01", "value": 5.25},
                    {"date": "2025-02-01", "value": 5.00},
                ],
                "cpi": [
                    {"date": "2025-01-01", "value": 3.20},
                ],
            },
        }
        monkeypatch.setattr(
            "etl.market.fred.fetch_fred_data",
            lambda _key: fake_fred,
        )

        conn = get_connection(empty_db)
        try:
            _precompute_fred(conn)
            conn.commit()

            series_rows = conn.execute(
                "SELECT key, date, value FROM econ_series ORDER BY key, date"
            ).fetchall()
        finally:
            conn.close()

        assert series_rows == [
            ("cpi", "2025-01-01", 3.20),
            ("fedRate", "2025-01-01", 5.25),
            ("fedRate", "2025-02-01", 5.00),
        ]

    def test_none_from_api_is_noop(self, empty_db: Path, monkeypatch) -> None:
        """fetch_fred_data returning None (total failure) should not insert or raise."""
        monkeypatch.setenv("FRED_API_KEY", "fake-key")
        monkeypatch.setattr(
            "etl.market.fred.fetch_fred_data",
            lambda _key: None,
        )
        conn = get_connection(empty_db)
        try:
            _precompute_fred(conn)
            conn.commit()
            rows = conn.execute("SELECT * FROM econ_series").fetchall()
        finally:
            conn.close()
        assert rows == []
