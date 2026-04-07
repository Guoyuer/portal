"""Tests for SQLite schema creation and connection helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from generate_asset_snapshot.db import get_connection, ingest_fidelity_csv, init_db

EXPECTED_TABLES = frozenset({
    "fidelity_transactions",
    "daily_close",
    "empower_snapshots",
    "empower_funds",
    "qianji_balances",
    "computed_daily",
    "computed_prefix",
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


class TestIngestFidelity:
    @pytest.fixture()
    def db_path(self, tmp_path: Path) -> Path:
        p = tmp_path / "test.db"
        init_db(p)
        return p

    def test_ingest_sample_csv(self, db_path: Path, history_sample_csv: Path) -> None:
        count = ingest_fidelity_csv(db_path, history_sample_csv)
        assert count > 0
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
        conn.close()
        assert rows == count

    def test_overlap_replaces(self, db_path: Path, history_sample_csv: Path) -> None:
        ingest_fidelity_csv(db_path, history_sample_csv)
        count2 = ingest_fidelity_csv(db_path, history_sample_csv)
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
        conn.close()
        assert rows == count2  # replaced, not doubled
