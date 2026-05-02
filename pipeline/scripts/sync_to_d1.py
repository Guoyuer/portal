"""Sync local timemachine.db tables to Cloudflare D1.

Dumps the tables the Worker needs from the local SQLite database into a SQL
file, then executes it against D1 via wrangler CLI.

Default mode is **diff** (safe): INSERT OR IGNORE for append-only tables and
range-replace (delete-after-cutoff + re-insert) for fidelity/qianji. The
destructive full-replace path requires the explicit ``--full`` flag.

Requires: wrangler CLI authenticated (`wrangler login`)

Usage:
    python scripts/sync_to_d1.py                          # diff sync to remote D1 (safe default)
    python scripts/sync_to_d1.py --full                   # DESTRUCTIVE full-replace
    python scripts/sync_to_d1.py --since 2025-01-01       # diff sync with explicit cutoff
    python scripts/sync_to_d1.py --local                  # sync to local D1 (wrangler dev)
    python scripts/sync_to_d1.py --dry-run                # generate SQL but don't execute
"""

from __future__ import annotations

# ruff: noqa: E402, I001

import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────

_PROJECT_DIR = Path(__file__).resolve().parent.parent

# Make etl/ importable and load pipeline/.env before any os.environ lookups.
sys.path.insert(0, str(_PROJECT_DIR))
import etl.dotenv_loader  # noqa: F401  (side effect: load pipeline/.env)
from scripts._wrangler import (
    run_wrangler_command,
    run_wrangler_exec_file,
    run_wrangler_query,
    sql_escape,
)
from scripts.sync_policy import (
    AUTO_SINCE_LOOKBACK_DAYS as _AUTO_SINCE_LOOKBACK_DAYS,
    RANGE_TABLES as _RANGE_TABLES,
    TABLES_TO_SYNC,
    auto_derive_since as _auto_derive_since,
    sync_mode_for_table,
)

_DB_PATH = Path(os.environ.get("PORTAL_DB_PATH", str(_PROJECT_DIR / "data" / "timemachine.db")))
_WORKER_DIR = _PROJECT_DIR.parent / "worker"

# ── D1 schema alignment ────────────────────────────────────────────────────
#
# Local and D1 share one schema. Any ``ALTER TABLE ADD COLUMN`` in local
# propagates to D1 on the next sync — D1 ALTER is O(1) metadata (unlike
# MySQL, it doesn't rewrite the table), so auto-applying is safe. Each
# ALTER lands in ``sync_log`` so the sync output reads as an audit trail.
# The payload-exposure contract (what reaches the browser) lives in views,
# which project explicit column lists — so D1 can be a faithful mirror
# without leaking columns into the client.


def _column_add_ddl(local_conn: sqlite3.Connection, table: str, col: str) -> str:
    """Return the ``col type [NOT NULL] [DEFAULT X]`` fragment for ALTER ADD.

    Reads the local schema (``etl/db.py``'s CREATE TABLE, as introspected via
    PRAGMA table_info) so the column added to D1 has the same type and
    nullability as the local source of truth. SQLite requires that NOT NULL
    columns added via ALTER have a DEFAULT — we enforce that by raising if
    the local schema has a NOT NULL column without one.
    """
    for _cid, name, ctype, notnull, dflt, _pk in local_conn.execute(
        f"PRAGMA table_info({table})"  # noqa: S608 — table is a trusted constant
    ):
        if name != col:
            continue
        ctype_str = str(ctype) if ctype else "TEXT"
        parts = [str(name), ctype_str]
        # SQLite's ALTER TABLE ADD COLUMN rejects NOT NULL without a DEFAULT.
        # Emit an implicit ``DEFAULT ''`` for TEXT columns that lack one —
        # real ingest always populates these (they come from CSV fields), so
        # the default only affects pre-existing rows during the one-off
        # schema upgrade. For non-TEXT types we can't invent a safe default,
        # so raise and ask the author to declare one in etl/db.py.
        if notnull and dflt is None:
            if ctype_str.upper() == "TEXT":
                dflt = "''"
            else:
                msg = (
                    f"Local column {table}.{col} is {ctype_str} NOT NULL "
                    f"without a DEFAULT — D1 ALTER ADD COLUMN would reject "
                    f"it and no safe implicit default exists for this type. "
                    f"Add an explicit DEFAULT to etl/db.py."
                )
                raise RuntimeError(msg)
        if notnull:
            parts.append("NOT NULL")
        if dflt is not None:
            parts.append(f"DEFAULT {dflt}")
        return " ".join(parts)
    msg = f"Column {col} not found in local schema of {table}"
    raise RuntimeError(msg)


def _fetch_d1_table_columns(
    tables: list[str], *, local: bool,
) -> dict[str, set[str]]:
    """Return ``{table: {col_name, ...}}`` for each requested table in D1.

    One wrangler call instead of N sequential ``PRAGMA table_info(T)``
    spawns (each paying a ~1s Node cold-start tax). Tables absent from D1
    map to an empty set — the caller treats that as "apply init schema
    first".

    D1 supports ``PRAGMA table_info`` but not in table-valued form combined
    with ``UNION ALL`` or cross-joins (wrangler crashes with a stack-buffer
    overrun inside libuv when parsing such queries), so we route through
    ``sqlite_schema`` and let SQLite itself parse each ``CREATE TABLE`` in
    an in-memory DB — zero hand-rolled DDL parsing.
    """
    if not tables:
        return {}
    names_sql = ", ".join(sql_escape(t) for t in tables)
    rows = run_wrangler_query(
        "SELECT name, sql FROM sqlite_schema "
        f"WHERE type='table' AND name IN ({names_sql})",
        local=local,
    )
    result: dict[str, set[str]] = {t: set() for t in tables}
    for row in rows:
        tbl = str(row["name"])
        ddl = row.get("sql")
        if not ddl:
            continue
        probe = sqlite3.connect(":memory:")
        try:
            probe.execute(str(ddl))
            result[tbl] = {
                str(name)
                for _cid, name, *_ in probe.execute(
                    f"PRAGMA table_info({tbl})"  # noqa: S608 — trusted constant
                )
            }
        finally:
            probe.close()
    return result


def _ensure_d1_schema_aligned(
    local_conn: sqlite3.Connection, *, local: bool, dry_run: bool,
) -> None:
    """Add any local column missing from D1 via ALTER TABLE ADD COLUMN.

    Iterates every table in ``TABLES_TO_SYNC``, compares its local columns to
    D1's, and closes the gap so ``SELECT * FROM local → INSERT INTO D1``
    won't blow up on a "no such column" at runtime. No-op when schemas
    already agree. Skips tables missing from D1 entirely — too big a jump
    to auto-create; user should apply the init schema first.

    D1 columns are fetched in a single wrangler call via
    :func:`_fetch_d1_table_columns` — the previous per-table PRAGMA loop cost
    ~8 seconds of Node cold-start overhead on every sync.
    """
    d1_columns = _fetch_d1_table_columns(TABLES_TO_SYNC, local=local)
    for table in TABLES_TO_SYNC:
        local_cols = [r[1] for r in local_conn.execute(
            f"PRAGMA table_info({table})"  # noqa: S608 — trusted constant
        )]
        if not local_cols:
            # Local table missing (fresh DB with incomplete init?) — let the
            # sync's later SELECT surface the real error instead of masking.
            continue
        d1_cols = d1_columns.get(table, set())
        if not d1_cols:
            print(
                f"  ! Table {table} not found in D1 — apply init schema before re-running sync"
            )
            continue
        missing = [c for c in local_cols if c not in d1_cols]
        if not missing:
            continue
        for col in missing:
            ddl = _column_add_ddl(local_conn, table, col)
            alter_sql = f"ALTER TABLE {table} ADD COLUMN {ddl}"
            if dry_run:
                print(f"  [dry-run] Would align D1 schema: {alter_sql}")
            else:
                print(f"  Aligning D1 schema: {alter_sql}")
                run_wrangler_command(alter_sql, local=local)
                _append_sync_log_row(
                    op="alter",
                    table_name=table,
                    rows_affected=0,
                    description=alter_sql,
                    local=local,
                )


# ── Audit log ──────────────────────────────────────────────────────────────
#
# Every destructive op on D1 appends exactly one row to the ``sync_log``
# table. Append-only by convention (never DELETE) so future forensics can
# answer "what changed prod on YYYY-MM-DD?". The diff/full sync path emits
# its row as part of the batched SQL file (atomic with the data write);
# auto-ALTER and ad-hoc scripts INSERT via separate wrangler calls.


def _invocation_context() -> str:
    """``<hostname> <branch>@<short-sha>`` for the ``invocation`` column.

    Git info is best-effort — falls back to "unknown" when not in a repo or
    git isn't on PATH. The value goes into an audit log, so approximate is
    better than absent.
    """
    host = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "?"
    branch = "unknown"
    sha = "unknown"
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(_PROJECT_DIR), text=True, stderr=subprocess.DEVNULL,
        ).strip() or branch
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_PROJECT_DIR), text=True, stderr=subprocess.DEVNULL,
        ).strip() or sha
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass
    return f"{host} {branch}@{sha}"


def _sync_log_insert_sql(
    *,
    op: str,
    table_name: str | None,
    rows_affected: int | None,
    description: str,
    invocation: str | None = None,
) -> str:
    """Generate one ``INSERT INTO sync_log`` statement (no trailing newline).

    Caller chooses whether to append to a batched SQL file or execute via
    wrangler immediately (see :func:`_append_sync_log_row`).
    """
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    inv = invocation if invocation is not None else _invocation_context()
    values = ", ".join([
        sql_escape(ts), sql_escape(op), sql_escape(table_name),
        sql_escape(rows_affected), sql_escape(description), sql_escape(inv),
    ])
    return (
        "INSERT INTO sync_log (ts, op, table_name, rows_affected, description, invocation) "
        f"VALUES ({values});"
    )


def _sync_meta_insert_sql(*, last_sync: str, last_date: str) -> str:
    """Generate two ``INSERT OR REPLACE INTO sync_meta`` statements.

    ``sync_meta`` is a ``(key, value)`` key-value table with ``key`` as PK, so
    ``INSERT OR REPLACE`` is idempotent. Previous versions used
    ``DELETE FROM sync_meta`` followed by two ``INSERT`` rows, which opened a
    brief empty-table window between the wipe and the inserts. Values flow
    through :func:`sql_escape` so any future non-ASCII or quoted input is safe
    by construction, matching the rest of the generated SQL.
    """
    return (
        "INSERT OR REPLACE INTO sync_meta (key, value) VALUES "
        f"({sql_escape('last_sync')}, {sql_escape(last_sync)});\n"
        "INSERT OR REPLACE INTO sync_meta (key, value) VALUES "
        f"({sql_escape('last_date')}, {sql_escape(last_date)});"
    )


def _append_sync_log_row(
    *,
    op: str,
    table_name: str | None,
    rows_affected: int | None,
    description: str,
    local: bool,
    invocation: str | None = None,
) -> None:
    """Execute one ``INSERT INTO sync_log`` against D1 via wrangler.

    Used by standalone code paths (auto-ALTER, ad-hoc manual scripts) that
    don't build a batched SQL file. For the main sync path, use
    :func:`_sync_log_insert_sql` to append directly to the batch.
    """
    run_wrangler_command(
        _sync_log_insert_sql(
            op=op, table_name=table_name, rows_affected=rows_affected,
            description=description, invocation=invocation,
        ),
        local=local,
    )


# Sync policy lives in ``scripts.sync_policy`` so the pre-sync verifier and
# SQL generator cannot drift apart. Re-export the legacy private names above
# for existing unit tests and small internal callers.


# ── SQL generation ─────────────────────────────────────────────────────────────


def _dump_table(
    conn: sqlite3.Connection,
    table: str,
    *,
    mode: str = "full",
    date_expr: str | None = None,
    since: str | None = None,
) -> tuple[str, int]:
    """Generate SQL for one table. ``mode`` picks the write semantics:

    - ``"full"``   — DELETE FROM + INSERT INTO (wipe + replace).
    - ``"diff"``   — INSERT OR IGNORE only (append-only, date PK).
    - ``"range"``  — DELETE WHERE {date_expr} > {since} + INSERT INTO (range-
                     replace). Requires ``date_expr`` and ``since``.

    Returns ``(sql, row_count)``.
    """
    if mode == "range":
        if date_expr is None or since is None:
            msg = "mode='range' requires date_expr and since"
            raise ValueError(msg)
        cursor = conn.execute(f"SELECT * FROM {table} WHERE {date_expr} > ?", (since,))  # noqa: S608
    else:
        cursor = conn.execute(f"SELECT * FROM {table}")  # noqa: S608
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    # sql_escape quotes/escapes the cutoff identically to every row value, so
    # the generated DELETE can't be broken by a typo'd `--since` argument.
    if mode == "full":
        lines: list[str] = [f"DELETE FROM {table};"]
        insert_verb = "INSERT INTO"
    elif mode == "diff":
        lines = []
        insert_verb = "INSERT OR IGNORE INTO"
    elif mode == "range":
        lines = [f"DELETE FROM {table} WHERE {date_expr} > {sql_escape(since)};"]
        insert_verb = "INSERT INTO"
    else:
        msg = f"unknown mode: {mode!r}"
        raise ValueError(msg)

    cols_sql = ", ".join(columns)
    for row in rows:
        values = ", ".join(sql_escape(v) for v in row)
        lines.append(f"{insert_verb} {table} ({cols_sql}) VALUES ({values});")

    return "\n".join(lines), len(rows)


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync timemachine.db tables to Cloudflare D1 (default: diff mode)"
    )
    parser.add_argument("--local", action="store_true", help="Sync to local D1 (wrangler dev)")
    parser.add_argument("--dry-run", action="store_true", help="Generate SQL but don't execute")
    parser.add_argument(
        "--full",
        action="store_true",
        help="DESTRUCTIVE: full replace all tables (default is diff)",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="Cutoff date for range-replace (YYYY-MM-DD). Auto-derived from local data if omitted.",
    )
    return parser.parse_args()


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    args = _parse_args()

    if not _DB_PATH.exists():
        print(f"Error: database not found: {_DB_PATH}", file=sys.stderr)
        sys.exit(1)

    if not _WORKER_DIR.exists():
        print(f"Error: worker directory not found: {_WORKER_DIR}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = None  # ensure tuples

    # Local and D1 share one schema — close any column-level gap by ALTER-ing
    # D1 up to local's shape before the first INSERT references a column D1
    # doesn't yet have. Dry-run skips this entirely (both the read PRAGMA
    # and the ALTER are wrangler roundtrips — dry-run stays offline).
    if not args.dry_run:
        _ensure_d1_schema_aligned(conn, local=args.local, dry_run=False)

    # Local D1 is a dev mirror — diff mode would leave pre-window rows stale
    # if local was initially seeded from an older snapshot, drifting forever.
    # Force full-replace on --local so dev always matches prod.
    mode = "full" if (args.full or args.local) else "diff"
    since = args.since
    if mode == "diff" and since is None:
        since = _auto_derive_since(conn)
        print(f"  Auto-derived --since={since} (fidelity MAX(run_date) - {_AUTO_SINCE_LOOKBACK_DAYS} days)")

    if args.local and not args.full:
        print("  --local implies --full (local D1 always mirrors timemachine.db)")
    print(f"  Sync mode: {mode}")

    all_sql: list[str] = []
    total_rows = 0

    for table in TABLES_TO_SYNC:
        table_mode = sync_mode_for_table(table, full=(mode == "full"))
        if mode == "diff" and table_mode == "diff":
            sql, count = _dump_table(conn, table, mode="diff")
            print(f"  {table}: {count} rows (INSERT OR IGNORE)")
        elif mode == "diff" and table_mode == "range":
            sql, count = _dump_table(
                conn, table, mode="range", date_expr=_RANGE_TABLES[table], since=since,
            )
            print(f"  {table}: {count} rows (range-replace > {since})")
        else:
            sql, count = _dump_table(conn, table, mode="full")
            label = "full replace" if mode == "full" else "full replace (metadata table)"
            print(f"  {table}: {count} rows ({label})")
        all_sql.append(sql)
        total_rows += count

    # Sync metadata — last_sync timestamp and data coverage. See
    # :func:`_sync_meta_insert_sql` for the idempotency rationale.
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    last_date_row = conn.execute("SELECT MAX(date) FROM computed_daily").fetchone()
    last_date = last_date_row[0] if last_date_row and last_date_row[0] else ""
    all_sql.append(_sync_meta_insert_sql(last_sync=now, last_date=last_date))

    # Audit trail — one row per run, appended atomically with the data write.
    # ``--since`` is captured in the description so forensics can reconstruct
    # exactly which date window each sync touched.
    since_hint = f" since={since}" if mode == "diff" and since else ""
    all_sql.append(_sync_log_insert_sql(
        op=mode, table_name=None, rows_affected=total_rows,
        description=(
            f"{mode} sync: {len(TABLES_TO_SYNC)} tables, {total_rows} rows{since_hint}"
        ),
    ))

    conn.close()

    combined = "\n\n".join(all_sql) + "\n"

    if args.dry_run:
        print(f"\n[dry-run] Generated {total_rows} total rows, SQL not executed")
        print(f"[dry-run] SQL size: {len(combined):,} bytes")
        print("[dry-run] --- SQL preview ---")
        print(combined)
        return

    # Write to temp file and execute via wrangler. NamedTemporaryFile gets us
    # a unique filename safely (mktemp() is deprecated and racy when two
    # runners collide — e.g. Task Scheduler + manual invocation).
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".sql", prefix="d1_sync_", delete=False,
    ) as tmpf:
        tmpf.write(combined)
        tmp = Path(tmpf.name)
    try:
        print(f"\nExecuting {total_rows} rows against D1 ({len(combined):,} bytes)...")
        try:
            run_wrangler_exec_file(tmp, local=args.local)
        except RuntimeError as e:
            print(f"Error: wrangler failed:\n{e}", file=sys.stderr)
            sys.exit(1)
        print("D1 sync complete.")
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
