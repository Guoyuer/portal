"""Tests for diff-based sync SQL generation."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from etl.db import get_connection
from scripts._wrangler import sql_escape
from scripts.sync_to_d1 import (
    TABLES_TO_SYNC,
    _column_add_ddl,
    _dump_table,
    _ensure_d1_schema_aligned,
    _fetch_d1_table_columns,
    _invocation_context,
    _sync_log_insert_sql,
    _sync_meta_insert_sql,
)


@pytest.fixture()
def db(empty_db):
    conn = get_connection(empty_db)
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
    return empty_db


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
        assert sql_escape(None) == "NULL"

    def test_number(self):
        assert sql_escape(42) == "42"

    def test_float(self):
        assert sql_escape(3.14) == "3.14"

    def test_string_with_quotes(self):
        assert sql_escape("it's") == "'it''s'"

    def test_plain_string(self):
        assert sql_escape("hello") == "'hello'"


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

    def test_text_not_null_without_default_falls_back_to_empty_string(self, tmp_path):
        """TEXT NOT NULL without a DEFAULT is common for CSV-ingested columns;
        auto-ALTER emits ``DEFAULT ''`` so legacy DBs (whose local schema
        predates the explicit default declaration) can still be aligned
        to D1 without a manual schema migration."""
        db_path = tmp_path / "tiny.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE t (a TEXT NOT NULL)")
        ddl = _column_add_ddl(conn, "t", "a")
        conn.close()
        assert ddl == "a TEXT NOT NULL DEFAULT ''"

    def test_non_text_not_null_without_default_raises(self, tmp_path):
        """REAL/INTEGER NOT NULL without a DEFAULT has no safe implicit value
        — fail loudly and ask the author to declare one."""
        db_path = tmp_path / "tiny.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE t (a REAL NOT NULL)")
        with pytest.raises(RuntimeError, match=r"NOT NULL without a DEFAULT"):
            _column_add_ddl(conn, "t", "a")
        conn.close()


class TestEnsureD1SchemaAligned:
    """End-to-end behaviour of the auto-ALTER path, wrangler calls mocked.

    The function fetches every table's D1 shape in one ``sqlite_schema``
    SELECT (not per-table PRAGMA), so the fake ``run_wrangler_query`` returns
    ``{name, sql}`` rows reflecting D1's state relative to local's PRAGMA —
    matching local = no ALTER, missing local column = one ALTER per gap.
    """

    @staticmethod
    def _local_cols(conn: sqlite3.Connection, table: str) -> list[str]:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]  # noqa: S608

    def _make_sqlite_schema_fake(
        self, db, drop_col: tuple[str, str] | None = None, empty_table: str | None = None,
    ):
        """Fake ``run_wrangler_query`` returning D1's ``sqlite_schema`` shape.

        Each row is ``{"name": <table>, "sql": <minimal CREATE TABLE DDL>}``.
        ``drop_col=(table, col)`` removes that column from that table's DDL;
        ``empty_table`` omits that table entirely (simulates "not in D1").

        The DDL is minimal on purpose — type/default/PK details aren't read
        by the production helper (only column names are), so the fake stays
        simple and doesn't have to reconstruct full CREATE TABLE syntax.
        """
        call_count = {"n": 0}

        def fake_query(sql: str, *, local: bool = False, db_name: str = "portal-db") -> list[dict]:
            call_count["n"] += 1
            assert "sqlite_schema" in sql, f"unexpected query: {sql}"
            # Real D1 honors `WHERE name IN (...)` — mirror that by only
            # returning rows for tables the production code actually
            # requested. Without this, the fake would ship
            # ``sqlite_sequence`` (autoinc bookkeeping table that SQLite
            # refuses to re-CREATE in the probe DB).
            conn = sqlite3.connect(str(db))
            rows: list[dict] = []
            try:
                for name in TABLES_TO_SYNC:
                    if empty_table is not None and name == empty_table:
                        continue
                    cols = self._local_cols(conn, name)
                    if not cols:
                        continue  # table missing from local DB fixture
                    if drop_col is not None and name == drop_col[0]:
                        cols = [c for c in cols if c != drop_col[1]]
                    ddl = (
                        f"CREATE TABLE {name} ("
                        + ", ".join(f"{c} TEXT" for c in cols)
                        + ")"
                    )
                    rows.append({"name": name, "sql": ddl})
            finally:
                conn.close()
            return rows

        fake_query.call_count = call_count  # type: ignore[attr-defined]
        return fake_query

    def test_single_wrangler_roundtrip(self, db, monkeypatch):
        """Schema align must issue exactly ONE read to D1, not one per table."""
        from scripts import sync_to_d1

        fake = self._make_sqlite_schema_fake(db)
        monkeypatch.setattr(sync_to_d1, "run_wrangler_query", fake)
        monkeypatch.setattr(
            sync_to_d1, "run_wrangler_command",
            lambda sql, *, local=False, db_name="portal-db": None,
        )

        conn = sqlite3.connect(str(db))
        _ensure_d1_schema_aligned(conn, local=False, dry_run=False)
        conn.close()

        assert fake.call_count["n"] == 1  # type: ignore[attr-defined]

    def test_no_alter_when_d1_mirrors_local(self, db, monkeypatch):
        from scripts import sync_to_d1

        calls: list[str] = []

        def fake_exec(sql: str, *, local: bool = False, db_name: str = "portal-db") -> None:
            calls.append(sql)

        monkeypatch.setattr(sync_to_d1, "run_wrangler_query", self._make_sqlite_schema_fake(db))
        monkeypatch.setattr(sync_to_d1, "run_wrangler_command", fake_exec)

        conn = sqlite3.connect(str(db))
        _ensure_d1_schema_aligned(conn, local=False, dry_run=False)
        conn.close()

        assert calls == []

    def test_alter_issued_for_missing_column(self, db, monkeypatch):
        from scripts import sync_to_d1

        calls: list[str] = []

        def fake_exec(sql: str, *, local: bool = False, db_name: str = "portal-db") -> None:
            calls.append(sql)

        # Pretend qianji_transactions is missing "category" on D1.
        # (category is TEXT NOT NULL DEFAULT '' locally — a valid ALTER target.)
        monkeypatch.setattr(
            sync_to_d1, "run_wrangler_query",
            self._make_sqlite_schema_fake(db, drop_col=("qianji_transactions", "category")),
        )
        monkeypatch.setattr(sync_to_d1, "run_wrangler_command", fake_exec)

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

        # Pretend daily_close is missing "close" (REAL NOT NULL, no default).
        monkeypatch.setattr(
            sync_to_d1, "run_wrangler_query",
            self._make_sqlite_schema_fake(db, drop_col=("daily_close", "close")),
        )

        conn = sqlite3.connect(str(db))
        with pytest.raises(RuntimeError, match=r"NOT NULL without a DEFAULT"):
            _ensure_d1_schema_aligned(conn, local=False, dry_run=False)
        conn.close()

    def test_dry_run_logs_but_does_not_issue(self, db, monkeypatch, capsys):
        from scripts import sync_to_d1

        exec_calls: list[str] = []

        def fake_exec(sql: str, *, local: bool = False, db_name: str = "portal-db") -> None:
            exec_calls.append(sql)

        monkeypatch.setattr(
            sync_to_d1, "run_wrangler_query",
            self._make_sqlite_schema_fake(db, drop_col=("qianji_transactions", "category")),
        )
        monkeypatch.setattr(sync_to_d1, "run_wrangler_command", fake_exec)

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

        def fake_exec(sql: str, *, local: bool = False, db_name: str = "portal-db") -> None:
            exec_calls.append(sql)

        monkeypatch.setattr(
            sync_to_d1, "run_wrangler_query",
            self._make_sqlite_schema_fake(db, empty_table="daily_close"),
        )
        monkeypatch.setattr(sync_to_d1, "run_wrangler_command", fake_exec)

        conn = sqlite3.connect(str(db))
        _ensure_d1_schema_aligned(conn, local=False, dry_run=False)
        conn.close()

        assert exec_calls == []
        captured = capsys.readouterr()
        assert "daily_close not found in D1" in captured.out


class TestFetchD1TableColumns:
    """Single-roundtrip helper for schema-align: parses D1's CREATE TABLE DDL
    via in-memory SQLite and returns {table: set[col_names]}."""

    def test_empty_list_returns_empty_dict(self, monkeypatch):
        from scripts import sync_to_d1

        called = {"n": 0}

        def fake_query(sql: str, *, local: bool = False, db_name: str = "portal-db") -> list[dict]:
            called["n"] += 1
            return []

        monkeypatch.setattr(sync_to_d1, "run_wrangler_query", fake_query)
        assert _fetch_d1_table_columns([], local=False) == {}
        assert called["n"] == 0  # no network call for empty input

    def test_parses_create_table_via_inmemory_sqlite(self, monkeypatch):
        """A realistic CREATE TABLE (NOT NULL, DEFAULT, composite PK) must be
        parsed correctly — we delegate to SQLite itself, not a regex."""
        from scripts import sync_to_d1

        def fake_query(sql: str, *, local: bool = False, db_name: str = "portal-db") -> list[dict]:
            assert "sqlite_schema" in sql
            return [
                {
                    "name": "daily_close",
                    "sql": (
                        "CREATE TABLE daily_close ("
                        "symbol TEXT NOT NULL, "
                        "date   TEXT NOT NULL, "
                        "close  REAL NOT NULL, "
                        "PRIMARY KEY (symbol, date))"
                    ),
                },
                {
                    "name": "categories",
                    "sql": (
                        "CREATE TABLE categories ("
                        "name TEXT PRIMARY KEY, "
                        "kind TEXT NOT NULL DEFAULT 'expense')"
                    ),
                },
            ]

        monkeypatch.setattr(sync_to_d1, "run_wrangler_query", fake_query)

        result = _fetch_d1_table_columns(["daily_close", "categories"], local=False)
        assert result == {
            "daily_close": {"symbol", "date", "close"},
            "categories": {"name", "kind"},
        }

    def test_missing_table_maps_to_empty_set(self, monkeypatch):
        """Tables D1 didn't return land as empty sets so callers can detect
        them uniformly (vs crashing on KeyError)."""
        from scripts import sync_to_d1

        def fake_query(sql: str, *, local: bool = False, db_name: str = "portal-db") -> list[dict]:
            return [{"name": "categories", "sql": "CREATE TABLE categories (name TEXT)"}]

        monkeypatch.setattr(sync_to_d1, "run_wrangler_query", fake_query)
        result = _fetch_d1_table_columns(["categories", "absent_table"], local=False)
        assert result["categories"] == {"name"}
        assert result["absent_table"] == set()

    def test_single_wrangler_call(self, monkeypatch):
        """All requested tables come back in ONE query — explicit regression
        guard against the old per-table PRAGMA loop."""
        from scripts import sync_to_d1

        called: list[str] = []

        def fake_query(sql: str, *, local: bool = False, db_name: str = "portal-db") -> list[dict]:
            called.append(sql)
            return []

        monkeypatch.setattr(sync_to_d1, "run_wrangler_query", fake_query)
        _fetch_d1_table_columns(["a", "b", "c"], local=False)
        assert len(called) == 1
        # The query must filter by the requested table names (so D1 doesn't
        # ship every table's DDL on every sync).
        assert "'a'" in called[0] and "'b'" in called[0] and "'c'" in called[0]

    def test_row_with_null_sql_is_skipped(self, monkeypatch):
        """Defensive: if D1 returns a row with NULL/empty sql (shouldn't
        happen for real tables, but views or malformed schemas can), don't
        crash — just leave that table's column set empty."""
        from scripts import sync_to_d1

        def fake_query(sql: str, *, local: bool = False, db_name: str = "portal-db") -> list[dict]:
            return [
                {"name": "weird", "sql": None},
                {"name": "ok", "sql": "CREATE TABLE ok (x TEXT)"},
            ]

        monkeypatch.setattr(sync_to_d1, "run_wrangler_query", fake_query)
        result = _fetch_d1_table_columns(["weird", "ok"], local=False)
        assert result["weird"] == set()
        assert result["ok"] == {"x"}



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


class TestSyncMetaInsert:
    """``sync_meta`` write path — last_sync + last_date key-value rows."""

    def test_uses_insert_or_replace_not_delete(self):
        """The emitted SQL must not wipe the table first; INSERT OR REPLACE is
        idempotent against the (key) PK and avoids a brief empty-table window."""
        sql = _sync_meta_insert_sql(last_sync="2026-04-19T12:00:00Z", last_date="2026-04-17")
        assert "DELETE" not in sql.upper()
        assert sql.count("INSERT OR REPLACE INTO sync_meta") == 2

    def test_emits_both_last_sync_and_last_date(self):
        sql = _sync_meta_insert_sql(last_sync="2026-04-19T12:00:00Z", last_date="2026-04-17")
        assert "'last_sync'" in sql
        assert "'2026-04-19T12:00:00Z'" in sql
        assert "'last_date'" in sql
        assert "'2026-04-17'" in sql

    def test_values_routed_through_sql_escape(self):
        """A quote in the value (hypothetical, but future-proofing) must be
        doubled, not left to break the SQL parser."""
        sql = _sync_meta_insert_sql(last_sync="a'b", last_date="c'd")
        # Doubled single-quotes is SQLite's escape
        assert "'a''b'" in sql
        assert "'c''d'" in sql

    def test_applied_sql_writes_both_rows(self, db):
        """Apply the generated SQL to an in-memory DB and verify both rows land."""
        import sqlite3 as _sqlite3

        target = _sqlite3.connect(":memory:")
        target.executescript(
            "CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        )
        sql = _sync_meta_insert_sql(last_sync="2026-04-19T12:00:00Z", last_date="2026-04-17")
        target.executescript(sql)
        rows = dict(target.execute("SELECT key, value FROM sync_meta").fetchall())
        target.close()
        assert rows == {"last_sync": "2026-04-19T12:00:00Z", "last_date": "2026-04-17"}

    def test_applied_sql_is_idempotent(self):
        """Running the same sync twice must leave exactly one row per key."""
        import sqlite3 as _sqlite3

        target = _sqlite3.connect(":memory:")
        target.executescript(
            "CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        )
        sql = _sync_meta_insert_sql(last_sync="2026-04-19T12:00:00Z", last_date="2026-04-17")
        target.executescript(sql)
        target.executescript(sql)
        count = target.execute("SELECT COUNT(*) FROM sync_meta").fetchone()[0]
        target.close()
        assert count == 2  # exactly last_sync + last_date, no duplicates
