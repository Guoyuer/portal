"""Tests for econ_series table: ingest and roundtrip."""
from __future__ import annotations

from pathlib import Path

from etl.db import get_connection
from tests.fixtures import ingest_econ_series

SAMPLE_SERIES = {
    "fedFundsRate": [
        {"date": "2025-01", "value": 4.33},
        {"date": "2025-02", "value": 4.33},
        {"date": "2025-03", "value": 4.25},
    ],
    "cpiYoy": [
        {"date": "2025-01", "value": 3.0},
        {"date": "2025-02", "value": 2.8},
    ],
}


class TestIngestEconSeries:
    def test_returns_row_count(self, empty_db: Path) -> None:
        count = ingest_econ_series(empty_db, SAMPLE_SERIES)
        assert count == 5

    def test_roundtrip_values(self, empty_db: Path) -> None:
        ingest_econ_series(empty_db, SAMPLE_SERIES)
        conn = get_connection(empty_db)
        rows = conn.execute(
            "SELECT key, date, value FROM econ_series ORDER BY key, date"
        ).fetchall()
        conn.close()
        assert len(rows) == 5
        assert rows[0] == ("cpiYoy", "2025-01", 3.0)
        assert rows[-1] == ("fedFundsRate", "2025-03", 4.25)

    def test_reingest_replaces_data(self, empty_db: Path) -> None:
        ingest_econ_series(empty_db, SAMPLE_SERIES)
        new_series = {"vix": [{"date": "2025-01", "value": 18.5}]}
        count = ingest_econ_series(empty_db, new_series)
        assert count == 1
        conn = get_connection(empty_db)
        rows = conn.execute("SELECT key FROM econ_series").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "vix"

    def test_empty_series(self, empty_db: Path) -> None:
        count = ingest_econ_series(empty_db, {})
        assert count == 0

    def test_inline_econ_series_insert(self, empty_db: Path) -> None:
        """Verify the inline SQL pattern used by precompute_market works."""
        conn = get_connection(empty_db)
        conn.execute("DELETE FROM econ_series")
        conn.execute(
            "INSERT INTO econ_series (key, date, value) VALUES (?, ?, ?)",
            ("fedFundsRate", "2025-01", 4.33),
        )
        conn.commit()
        row = conn.execute("SELECT key, date, value FROM econ_series").fetchone()
        conn.close()
        assert row == ("fedFundsRate", "2025-01", 4.33)
