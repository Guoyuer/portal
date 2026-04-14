"""Tests for diff-based sync SQL generation."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl.db import get_connection, init_db
from scripts.sync_to_d1 import (
    _check_d1_column_drift,
    _dump_table,
    _escape,
)


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
        sql, count = _dump_table(conn, "computed_daily", mode="full")
        conn.close()
        assert "DELETE FROM computed_daily;" in sql
        assert "INSERT INTO computed_daily" in sql
        assert count == 2


class TestDiffMode:
    def test_insert_or_ignore_no_delete(self, db):
        conn = sqlite3.connect(str(db))
        sql, count = _dump_table(conn, "computed_daily", mode="diff")
        conn.close()
        assert "DELETE" not in sql
        assert "INSERT OR IGNORE" in sql
        assert count == 2

    def test_diff_idempotent_on_target(self, db):
        """INSERT OR IGNORE on a DB with existing rows should not duplicate."""
        conn = sqlite3.connect(str(db))
        sql, _ = _dump_table(conn, "computed_daily", mode="diff")
        conn.close()
        target = sqlite3.connect(str(db))
        target.executescript(sql)
        count = target.execute("SELECT COUNT(*) FROM computed_daily").fetchone()[0]
        target.close()
        assert count == 2  # no duplicates


class TestRangeReplace:
    def test_deletes_after_since_and_inserts(self, db):
        conn = sqlite3.connect(str(db))
        sql, count = _dump_table(
            conn, "qianji_transactions", mode="range", date_expr="date", since="2025-01-01",
        )
        conn.close()
        assert "DELETE FROM qianji_transactions WHERE date > '2025-01-01';" in sql
        assert "INSERT INTO qianji_transactions" in sql
        assert count == 1  # only 2025-01-02

    def test_no_rows_after_since(self, db):
        conn = sqlite3.connect(str(db))
        sql, count = _dump_table(
            conn, "qianji_transactions", mode="range", date_expr="date", since="2025-12-31",
        )
        conn.close()
        assert count == 0
        assert "DELETE FROM qianji_transactions" in sql

    def test_range_requires_date_expr_and_since(self, db):
        conn = sqlite3.connect(str(db))
        with pytest.raises(ValueError, match=r"range.*date_expr.*since"):
            _dump_table(conn, "qianji_transactions", mode="range")
        conn.close()

    def test_unknown_mode_raises(self, db):
        conn = sqlite3.connect(str(db))
        with pytest.raises(ValueError, match=r"unknown mode"):
            _dump_table(conn, "qianji_transactions", mode="bogus")
        conn.close()


class TestGenSchema:
    def test_gen_schema_includes_sync_meta(self):
        """gen_schema_sql output must include sync_meta table."""
        schema = Path(__file__).resolve().parent.parent.parent.parent / "worker" / "schema.sql"
        if not schema.exists():
            pytest.skip("schema.sql not found")
        text = schema.read_text()
        assert "sync_meta" in text, "sync_meta table missing from generated schema"


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


class TestCheckD1ColumnDrift:
    """Tripwire: fire when a local schema column isn't explicitly synced or omitted."""

    def test_clean_schema_passes(self, db):
        """Real local DB (per `init_db`) has every column declared in either bucket."""
        conn = sqlite3.connect(str(db))
        _check_d1_column_drift(conn)  # must not raise
        conn.close()

    def test_new_unclassified_column_raises(self, db, monkeypatch):
        """A column in the schema that's in neither _D1_COLUMNS nor _D1_OMITTED → raise."""
        conn = sqlite3.connect(str(db))
        conn.execute("ALTER TABLE fidelity_transactions ADD COLUMN new_field TEXT")
        conn.commit()

        with pytest.raises(RuntimeError, match=r"new_field"):
            _check_d1_column_drift(conn)
        conn.close()

    def test_declared_column_missing_from_schema_raises(self, monkeypatch):
        """_D1_COLUMNS references a column that doesn't exist → raise."""
        import tempfile

        from etl.db import init_db
        from scripts import sync_to_d1
        tmpdir = tempfile.mkdtemp()
        db = Path(tmpdir) / "tm.db"
        init_db(db)
        conn = sqlite3.connect(str(db))

        bogus = {"daily_close": ["symbol", "date", "close", "does_not_exist"]}
        monkeypatch.setattr(sync_to_d1, "_D1_COLUMNS", bogus)
        with pytest.raises(RuntimeError, match=r"does_not_exist"):
            _check_d1_column_drift(conn)
        conn.close()
