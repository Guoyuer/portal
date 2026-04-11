"""Tests for diff-based sync SQL generation."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from generate_asset_snapshot.db import get_connection, init_db
from scripts.sync_to_d1 import _dump_table, _dump_table_diff, _dump_table_range, _escape


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "test.db"
    init_db(p)
    conn = get_connection(p)
    # Insert test data
    conn.execute(
        "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
        " VALUES ('2025-01-01', 100, 50, 20, 10, 20)"
    )
    conn.execute(
        "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
        " VALUES ('2025-01-02', 110, 55, 22, 11, 22)"
    )
    conn.execute("INSERT INTO qianji_transactions (date, type, category, amount) VALUES ('2025-01-01', 'expense', 'Meals', 15)")
    conn.execute("INSERT INTO qianji_transactions (date, type, category, amount) VALUES ('2025-01-02', 'expense', 'Transport', 5)")
    conn.commit()
    conn.close()
    return p


class TestFullMode:
    def test_generates_delete_and_insert(self, db):
        conn = sqlite3.connect(str(db))
        sql, count = _dump_table(conn, "computed_daily")
        conn.close()
        assert "DELETE FROM computed_daily;" in sql
        assert "INSERT INTO computed_daily" in sql
        assert count == 2


class TestDiffMode:
    def test_insert_or_ignore_no_delete(self, db):
        conn = sqlite3.connect(str(db))
        sql, count = _dump_table_diff(conn, "computed_daily")
        conn.close()
        assert "DELETE" not in sql
        assert "INSERT OR IGNORE" in sql
        assert count == 2

    def test_diff_idempotent_on_target(self, db):
        """INSERT OR IGNORE on a DB with existing rows should not duplicate."""
        conn = sqlite3.connect(str(db))
        sql, _ = _dump_table_diff(conn, "computed_daily")
        conn.close()
        # Execute the SQL on the same DB (simulating D1)
        target = sqlite3.connect(str(db))
        target.executescript(sql)
        count = target.execute("SELECT COUNT(*) FROM computed_daily").fetchone()[0]
        target.close()
        assert count == 2  # no duplicates


class TestRangeReplace:
    def test_deletes_after_since_and_inserts(self, db):
        conn = sqlite3.connect(str(db))
        sql, count = _dump_table_range(conn, "qianji_transactions", "date", "2025-01-01")
        conn.close()
        assert "DELETE FROM qianji_transactions WHERE date > '2025-01-01';" in sql
        assert "INSERT INTO qianji_transactions" in sql
        assert count == 1  # only 2025-01-02

    def test_no_rows_after_since(self, db):
        conn = sqlite3.connect(str(db))
        sql, count = _dump_table_range(conn, "qianji_transactions", "date", "2025-12-31")
        conn.close()
        assert count == 0
        assert "DELETE FROM qianji_transactions" in sql


class TestEscape:
    def test_none(self):
        assert _escape(None) == "NULL"

    def test_number(self):
        assert _escape(42) == "42"

    def test_float(self):
        assert _escape(3.14) == "3.14"

    def test_string_with_quotes(self):
        assert _escape("it's") == "'it''s'"

    def test_plain_string(self):
        assert _escape("hello") == "'hello'"
