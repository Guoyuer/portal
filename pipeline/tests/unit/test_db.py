"""Tests for SQLite schema creation and connection helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from generate_asset_snapshot.db import (
    get_connection,
    ingest_prices,
    ingest_qianji_transactions,
    init_db,
)

EXPECTED_TABLES = frozenset({
    "fidelity_transactions",
    "daily_close",
    "empower_snapshots",
    "empower_funds",
    "empower_contributions",
    "qianji_balances",
    "qianji_transactions",
    "computed_daily",
    "computed_daily_tickers",
    "computed_market_indices",
    "computed_market_indicators",
    "computed_holdings_detail",
    "econ_series",
    "replay_checkpoint",
    "calibration_log",
    "categories",
})


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    return {r[0] for r in rows}


def _index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'").fetchall()
    return {r[0] for r in rows}


class TestInitDb:
    def test_creates_all_tables(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
        assert _table_names(conn) == EXPECTED_TABLES
        conn.close()

    def test_idempotent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        init_db(db_path)  # second call should not raise
        conn = get_connection(db_path)
        assert _table_names(conn) == EXPECTED_TABLES
        conn.close()

    def test_creates_indexes(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
        indexes = _index_names(conn)
        assert "idx_fidelity_date" in indexes
        assert "idx_fidelity_acct_sym" in indexes
        assert "idx_daily_close_date" in indexes
        assert "idx_daily_tickers_date" in indexes
        assert "idx_qianji_txn_date" in indexes
        conn.close()

    def test_computed_daily_has_liabilities(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net, liabilities)"
            " VALUES ('2025-01-02', 100000, 55000, 15000, 3000, 27000, -500)"
        )
        row = conn.execute("SELECT liabilities FROM computed_daily WHERE date='2025-01-02'").fetchone()
        assert row[0] == -500
        conn.close()

    def test_computed_daily_tickers_schema(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
        conn.execute(
            "INSERT INTO computed_daily_tickers (date, ticker, value, category, subtype, cost_basis, gain_loss, gain_loss_pct)"
            " VALUES ('2025-01-02', 'VOO', 50000, 'US Equity', 'broad', 40000, 10000, 25.0)"
        )
        row = conn.execute("SELECT * FROM computed_daily_tickers WHERE date='2025-01-02' AND ticker='VOO'").fetchone()
        assert row == ('2025-01-02', 'VOO', 50000, 'US Equity', 'broad', 40000, 10000, 25.0)
        conn.close()


class TestGetConnection:
    def test_wal_mode(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_foreign_keys_enabled(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
        conn.close()


class TestIngestPrices:
    @pytest.fixture()
    def db_path(self, tmp_path: Path) -> Path:
        p = tmp_path / "test.db"
        init_db(p)
        return p

    def test_ingest_prices(self, db_path: Path) -> None:
        ingest_prices(db_path, {"VOO": {"2025-01-02": 500.0, "2025-01-03": 502.0}})
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM daily_close").fetchone()[0]
        conn.close()
        assert rows == 2

    def test_upsert_prices(self, db_path: Path) -> None:
        ingest_prices(db_path, {"VOO": {"2025-01-02": 500.0}})
        ingest_prices(db_path, {"VOO": {"2025-01-02": 501.0}})
        conn = sqlite3.connect(str(db_path))
        val = conn.execute("SELECT close FROM daily_close WHERE symbol='VOO'").fetchone()[0]
        conn.close()
        assert val == 501.0


class TestIngestQianjiTransactions:
    @pytest.fixture()
    def db_path(self, tmp_path: Path) -> Path:
        p = tmp_path / "test.db"
        init_db(p)
        return p

    def test_ingest_records(self, db_path: Path) -> None:
        records = [
            {"date": "2025-03-01", "type": "income", "category": "Salary", "amount": 5000.0, "account_from": "Checking", "note": ""},
            {"date": "2025-03-05", "type": "expense", "category": "Rent", "amount": 1500.0, "account_from": "Checking", "note": ""},
        ]
        count = ingest_qianji_transactions(db_path, records)
        assert count == 2

    def test_clears_and_replaces(self, db_path: Path) -> None:
        records = [{"date": "2025-03-01", "type": "income", "category": "Salary", "amount": 5000.0, "account_from": "Checking", "note": ""}]
        ingest_qianji_transactions(db_path, records)
        new_records = [{"date": "2025-04-01", "type": "expense", "category": "Food", "amount": 100.0, "account_from": "Checking", "note": ""}]
        count = ingest_qianji_transactions(db_path, new_records)
        assert count == 1  # old rows cleared

    def test_empty_records(self, db_path: Path) -> None:
        count = ingest_qianji_transactions(db_path, [])
        assert count == 0
