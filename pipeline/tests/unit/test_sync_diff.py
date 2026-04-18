"""Tests for diff-based sync SQL generation."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl.db import get_connection, init_db
from scripts.sync_to_d1 import (
    _column_add_ddl,
    _dump_table,
    _ensure_d1_schema_aligned,
    _escape,
    _invocation_context,
    _sync_log_insert_sql,
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


class TestColumnAddDdl:
    """``_column_add_ddl`` reconstructs the ``<col> <type> ...`` fragment from
    the local schema so ALTER TABLE ADD COLUMN on D1 matches exactly."""

    def test_text_with_default(self, db):
        conn = sqlite3.connect(str(db))
        # qianji_transactions.category is TEXT NOT NULL DEFAULT '' — stable
        # schema example that exercises the NOT NULL + DEFAULT emit path.
        ddl = _column_add_ddl(conn, "qianji_transactions", "category")
        conn.close()
        assert ddl.startswith("category TEXT")
        assert "NOT NULL" in ddl
        assert "DEFAULT" in ddl

    def test_nullable_column_skips_not_null(self, db):
        conn = sqlite3.connect(str(db))
        # fidelity_transactions.action_kind is TEXT (nullable, no default).
        ddl = _column_add_ddl(conn, "fidelity_transactions", "action_kind")
        conn.close()
        assert ddl.startswith("action_kind TEXT")
        assert "NOT NULL" not in ddl
        assert "DEFAULT" not in ddl

    def test_missing_column_raises(self, db):
        conn = sqlite3.connect(str(db))
        with pytest.raises(RuntimeError, match=r"not found"):
            _column_add_ddl(conn, "qianji_transactions", "does_not_exist")
        conn.close()

    def test_not_null_without_default_raises(self, tmp_path):
        """Adding a NOT NULL column without a DEFAULT on D1 is illegal — we
        fail loudly in advance rather than letting wrangler error at runtime."""
        db_path = tmp_path / "tiny.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE t (a TEXT NOT NULL)")
        with pytest.raises(RuntimeError, match=r"NOT NULL without a DEFAULT"):
            _column_add_ddl(conn, "t", "a")
        conn.close()


class TestEnsureD1SchemaAligned:
    """End-to-end behaviour of the auto-ALTER path, wrangler calls mocked.

    The function iterates every table in ``TABLES_TO_SYNC`` and ALTERs D1 up
    to the local shape, so the fakes below report D1's state relative to
    local's PRAGMA — matching local = no ALTER, missing local column = one
    ALTER per gap.
    """

    @staticmethod
    def _local_cols(conn: sqlite3.Connection, table: str) -> list[str]:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]  # noqa: S608

    def test_no_alter_when_d1_mirrors_local(self, db, monkeypatch):
        from scripts import sync_to_d1

        calls: list[str] = []

        def fake_pragma(table: str, *, local: bool) -> list[dict]:
            # Claim D1 already has every local column for every table.
            conn = sqlite3.connect(str(db))
            try:
                cols = self._local_cols(conn, table)
            finally:
                conn.close()
            return [{"name": c} for c in cols]

        def fake_exec(sql: str, *, local: bool) -> None:
            calls.append(sql)

        monkeypatch.setattr(sync_to_d1, "_wrangler_pragma", fake_pragma)
        monkeypatch.setattr(sync_to_d1, "_wrangler_exec_ddl", fake_exec)

        conn = sqlite3.connect(str(db))
        _ensure_d1_schema_aligned(conn, local=False, dry_run=False)
        conn.close()

        assert calls == []

    def test_alter_issued_for_missing_column(self, db, monkeypatch):
        from scripts import sync_to_d1

        calls: list[str] = []

        def fake_pragma(table: str, *, local: bool) -> list[dict]:
            # Pretend qianji_transactions is missing "category" on D1.
            # (category is TEXT NOT NULL DEFAULT '' locally — a valid ALTER target.)
            conn = sqlite3.connect(str(db))
            try:
                cols = self._local_cols(conn, table)
            finally:
                conn.close()
            if table == "qianji_transactions":
                cols = [c for c in cols if c != "category"]
            return [{"name": c} for c in cols]

        def fake_exec(sql: str, *, local: bool) -> None:
            calls.append(sql)

        monkeypatch.setattr(sync_to_d1, "_wrangler_pragma", fake_pragma)
        monkeypatch.setattr(sync_to_d1, "_wrangler_exec_ddl", fake_exec)

        conn = sqlite3.connect(str(db))
        _ensure_d1_schema_aligned(conn, local=False, dry_run=False)
        conn.close()

        # Each ALTER is paired with a matching sync_log INSERT (audit trail).
        assert len(calls) == 2
        assert calls[0].startswith("ALTER TABLE qianji_transactions ADD COLUMN category")
        assert "TEXT" in calls[0]
        assert "DEFAULT" in calls[0]
        assert calls[1].startswith("INSERT INTO sync_log")
        assert "'alter'" in calls[1]
        assert "'qianji_transactions'" in calls[1]

    def test_not_null_without_default_aborts_sync(self, db, monkeypatch):
        """A local column that's NOT NULL without a DEFAULT can't be ALTER-
        added on D1 — fail loudly rather than ship a sync that will crash
        mid-INSERT on D1."""
        from scripts import sync_to_d1

        def fake_pragma(table: str, *, local: bool) -> list[dict]:
            # Pretend daily_close is missing "close" (REAL NOT NULL, no default).
            conn = sqlite3.connect(str(db))
            try:
                cols = self._local_cols(conn, table)
            finally:
                conn.close()
            if table == "daily_close":
                cols = [c for c in cols if c != "close"]
            return [{"name": c} for c in cols]

        monkeypatch.setattr(sync_to_d1, "_wrangler_pragma", fake_pragma)

        conn = sqlite3.connect(str(db))
        with pytest.raises(RuntimeError, match=r"NOT NULL without a DEFAULT"):
            _ensure_d1_schema_aligned(conn, local=False, dry_run=False)
        conn.close()

    def test_dry_run_logs_but_does_not_issue(self, db, monkeypatch, capsys):
        from scripts import sync_to_d1

        exec_calls: list[str] = []

        def fake_pragma(table: str, *, local: bool) -> list[dict]:
            conn = sqlite3.connect(str(db))
            try:
                cols = self._local_cols(conn, table)
            finally:
                conn.close()
            if table == "qianji_transactions":
                cols = [c for c in cols if c != "category"]
            return [{"name": c} for c in cols]

        def fake_exec(sql: str, *, local: bool) -> None:
            exec_calls.append(sql)

        monkeypatch.setattr(sync_to_d1, "_wrangler_pragma", fake_pragma)
        monkeypatch.setattr(sync_to_d1, "_wrangler_exec_ddl", fake_exec)

        conn = sqlite3.connect(str(db))
        _ensure_d1_schema_aligned(conn, local=False, dry_run=True)
        conn.close()

        assert exec_calls == []
        captured = capsys.readouterr()
        assert "[dry-run]" in captured.out
        assert "ALTER TABLE qianji_transactions ADD COLUMN category" in captured.out

    def test_missing_table_warns_and_continues(self, db, monkeypatch, capsys):
        """When D1 reports an empty table, skip with a warning (too big a jump
        to auto-create the whole table)."""
        from scripts import sync_to_d1

        exec_calls: list[str] = []

        def fake_pragma(table: str, *, local: bool) -> list[dict]:
            if table == "daily_close":
                return []
            conn = sqlite3.connect(str(db))
            try:
                cols = self._local_cols(conn, table)
            finally:
                conn.close()
            return [{"name": c} for c in cols]

        def fake_exec(sql: str, *, local: bool) -> None:
            exec_calls.append(sql)

        monkeypatch.setattr(sync_to_d1, "_wrangler_pragma", fake_pragma)
        monkeypatch.setattr(sync_to_d1, "_wrangler_exec_ddl", fake_exec)

        conn = sqlite3.connect(str(db))
        _ensure_d1_schema_aligned(conn, local=False, dry_run=False)
        conn.close()

        assert exec_calls == []
        captured = capsys.readouterr()
        assert "daily_close not found in D1" in captured.out



class TestSyncLogInsert:
    """``sync_log`` write path — audit trail for every destructive D1 op."""

    def test_emits_well_formed_insert(self):
        sql = _sync_log_insert_sql(
            op="diff",
            table_name="qianji_transactions",
            rows_affected=19,
            description="diff sync: 9 tables, 19 rows",
            invocation="host branch@abc123",
        )
        assert sql.startswith(
            "INSERT INTO sync_log (ts, op, table_name, rows_affected, "
            "description, invocation) VALUES ("
        )
        assert sql.endswith(");")
        assert "'diff'" in sql
        assert "'qianji_transactions'" in sql
        assert "19" in sql
        assert "'diff sync: 9 tables, 19 rows'" in sql
        assert "'host branch@abc123'" in sql

    def test_escapes_single_quotes_in_description(self):
        """Audit messages with embedded quotes don't break SQL parsing."""
        sql = _sync_log_insert_sql(
            op="manual",
            table_name=None,
            rows_affected=None,
            description="can't say 'hello' here",
            invocation="x",
        )
        # Doubled single-quotes is SQLite's escape
        assert "'can''t say ''hello'' here'" in sql
        # NULLs for absent values
        assert ", NULL, NULL, " in sql

    def test_invocation_context_includes_host(self, monkeypatch):
        """Host from COMPUTERNAME/HOSTNAME is always present, git info is best-effort."""
        monkeypatch.setenv("COMPUTERNAME", "TEST-HOST")
        ctx = _invocation_context()
        assert ctx.startswith("TEST-HOST ")
        assert "@" in ctx  # <branch>@<sha> format

    def test_invocation_context_fallbacks_when_git_absent(self, monkeypatch):
        """When git isn't on PATH, fall back to 'unknown' instead of crashing."""
        # Force git lookup to fail by pointing PATH at an empty directory.
        monkeypatch.setenv("PATH", "")
        ctx = _invocation_context()
        assert "unknown@unknown" in ctx
