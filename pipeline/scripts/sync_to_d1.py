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

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────

_PROJECT_DIR = Path(__file__).resolve().parent.parent

# Make etl/ importable and load pipeline/.env before any os.environ lookups.
sys.path.insert(0, str(_PROJECT_DIR))
import etl.dotenv_loader  # noqa: E402, F401  (side effect: load pipeline/.env)

_DB_PATH = Path(os.environ.get("PORTAL_DB_PATH", str(_PROJECT_DIR / "data" / "timemachine.db")))
_WORKER_DIR = _PROJECT_DIR.parent / "worker"

# When --since is not supplied, derive cutoff as (latest fidelity run_date - N days).
# 60 days comfortably exceeds Fidelity's typical CSV export window.
_AUTO_SINCE_LOOKBACK_DAYS = 60

TABLES_TO_SYNC: list[str] = [
    "computed_daily",
    "computed_daily_tickers",
    "fidelity_transactions",
    "qianji_transactions",
    "computed_market_indices",
    "computed_holdings_detail",
    "econ_series",
    "daily_close",
    "categories",
]

# ── D1 schema alignment ────────────────────────────────────────────────────
#
# Local and D1 schemas are a single shared shape: every column in ``etl/db.py``
# is synced to D1, and any ``ALTER TABLE ADD COLUMN`` in local propagates to
# D1 on the next sync via the helpers below. D1 ALTER is an O(1) metadata
# op (unlike MySQL, it doesn't rewrite the table), so auto-applying is safe
# at the scale we care about. Each ALTER is logged so the sync output still
# reads as an audit trail.
#
# The payload-exposure contract (what actually reaches the browser) lives
# one layer up in views — they project explicit column lists, and
# ``test_views_no_banned_columns`` guards identifiers that must never appear
# in a view body. Keeping D1 a faithful mirror of local lets the exposure
# question stay where it belongs (the view) instead of splitting across two
# layers of partial-whitelist bookkeeping.
#
# The helpers below let ``main()`` detect the mismatch and apply ``ALTER TABLE
# ADD
# COLUMN`` automatically. D1's ALTER is an O(1) metadata op — unlike MySQL,
# it doesn't rewrite the table — so auto-applying is safe at the scale we
# care about. Each ALTER is logged so the sync output still reads as an
# audit trail.


def _wrangler_remote_flag(local: bool) -> str:
    return "--local" if local else "--remote"


def _wrangler_pragma(table: str, local: bool) -> list[dict[str, object]]:
    """Run ``PRAGMA table_info(<table>)`` against D1 and parse the JSON rows.

    Returns an empty list when the table doesn't exist on D1 — callers treat
    this as "skip, D1 is unbootstrapped" rather than trying to auto-create
    the table.
    """
    npx = shutil.which("npx")
    if npx is None:
        msg = "npx not found in PATH — install Node.js or add npm bin to PATH"
        raise RuntimeError(msg)
    result = subprocess.run(
        [npx, "wrangler", "d1", "execute", "portal-db", _wrangler_remote_flag(local),
         "--json", f"--command=PRAGMA table_info({table})"],
        cwd=str(_WORKER_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"wrangler PRAGMA failed for {table} (rc={result.returncode})\n"
            f"stderr:\n{result.stderr or '(empty)'}\n"
            f"stdout:\n{result.stdout or '(empty)'}"
        )
    data = json.loads(result.stdout)
    if isinstance(data, list) and data and "results" in data[0]:
        return list(data[0]["results"])
    return []


def _wrangler_exec_ddl(sql: str, local: bool) -> None:
    """Execute one DDL statement (ALTER TABLE ...) on D1. Raises on failure."""
    npx = shutil.which("npx")
    if npx is None:
        msg = "npx not found in PATH — install Node.js or add npm bin to PATH"
        raise RuntimeError(msg)
    result = subprocess.run(
        [npx, "wrangler", "d1", "execute", "portal-db", _wrangler_remote_flag(local),
         f"--command={sql}"],
        cwd=str(_WORKER_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"wrangler DDL failed (rc={result.returncode})\nSQL: {sql}\n"
            f"stderr:\n{result.stderr or '(empty)'}\n"
            f"stdout:\n{result.stdout or '(empty)'}"
        )


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
        parts = [str(name), str(ctype) if ctype else "TEXT"]
        if notnull and dflt is None:
            msg = (
                f"Local column {table}.{col} is NOT NULL without a DEFAULT — "
                f"D1 ALTER ADD COLUMN would reject it. Add a DEFAULT to etl/db.py."
            )
            raise RuntimeError(msg)
        if notnull:
            parts.append("NOT NULL")
        if dflt is not None:
            parts.append(f"DEFAULT {dflt}")
        return " ".join(parts)
    msg = f"Column {col} not found in local schema of {table}"
    raise RuntimeError(msg)


def _ensure_d1_schema_aligned(
    local_conn: sqlite3.Connection, *, local: bool, dry_run: bool,
) -> None:
    """Add any local column missing from D1 via ALTER TABLE ADD COLUMN.

    Iterates every table in ``TABLES_TO_SYNC``, compares its local columns to
    D1's, and closes the gap so ``SELECT * FROM local → INSERT INTO D1``
    won't blow up on a "no such column" at runtime. No-op when schemas
    already agree. Skips tables missing from D1 entirely — too big a jump
    to auto-create; user should apply the init schema first.
    """
    for table in TABLES_TO_SYNC:
        local_cols = [r[1] for r in local_conn.execute(
            f"PRAGMA table_info({table})"  # noqa: S608 — trusted constant
        )]
        if not local_cols:
            # Local table missing (fresh DB with incomplete init?) — let the
            # sync's later SELECT surface the real error instead of masking.
            continue
        d1_info = _wrangler_pragma(table, local=local)
        d1_cols = {str(row["name"]) for row in d1_info}
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
                _wrangler_exec_ddl(alter_sql, local=local)
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
# table. The table is append-only by convention (never DELETE) so future
# forensics can answer "what changed prod on YYYY-MM-DD?". The normal
# diff/full sync path emits its row as part of the generated SQL bundle
# (so it's atomic with the data write); auto-ALTER and ad-hoc manual
# scripts INSERT via separate wrangler calls using the same helpers.


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
        _escape(ts), _escape(op), _escape(table_name),
        _escape(rows_affected), _escape(description), _escape(inv),
    ])
    return (
        "INSERT INTO sync_log (ts, op, table_name, rows_affected, description, invocation) "
        f"VALUES ({values});"
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
    sql = _sync_log_insert_sql(
        op=op, table_name=table_name, rows_affected=rows_affected,
        description=description, invocation=invocation,
    )
    _wrangler_exec_ddl(sql, local=local)


# Tables that use INSERT OR IGNORE in diff mode (append-only, have date PK)
_DIFF_TABLES: set[str] = {"daily_close"}

# Tables that use range-replace in diff mode (delete after cutoff, reinsert).
# Value is a SQL expression that yields a YYYY-MM-DD–sortable string for date comparison.
#
# ``computed_daily`` + ``computed_daily_tickers`` sit here (not in
# ``_DIFF_TABLES``) so the local sync's authoritative rows physically replace
# any projected rows the nightly CI job wrote beyond the last local build —
# INSERT OR IGNORE would skip them and leave stale projections in D1.
_RANGE_TABLES: dict[str, str] = {
    "fidelity_transactions": "run_date",
    "qianji_transactions": "date",
    "computed_daily": "date",
    "computed_daily_tickers": "date",
}


# ── SQL generation ─────────────────────────────────────────────────────────────


def _escape(value: object) -> str:
    """Format a Python value as a SQL literal, handling NULLs and quoting."""
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    # String: escape single quotes by doubling them
    return "'" + str(value).replace("'", "''") + "'"


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

    # _escape quotes/escapes the cutoff identically to every row value, so the
    # generated DELETE can't be broken by a typo'd `--since` argument.
    if mode == "full":
        lines: list[str] = [f"DELETE FROM {table};"]
        insert_verb = "INSERT INTO"
    elif mode == "diff":
        lines = []
        insert_verb = "INSERT OR IGNORE INTO"
    elif mode == "range":
        lines = [f"DELETE FROM {table} WHERE {date_expr} > {_escape(since)};"]
        insert_verb = "INSERT INTO"
    else:
        msg = f"unknown mode: {mode!r}"
        raise ValueError(msg)

    cols_sql = ", ".join(columns)
    for row in rows:
        values = ", ".join(_escape(v) for v in row)
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


def _auto_derive_since(conn: sqlite3.Connection) -> str:
    """Derive a safe --since cutoff: latest fidelity run_date minus 60 days.

    This guarantees the range-replace window covers any realistic Fidelity
    CSV export period, so a newly-ingested CSV's date range is fully covered.
    """
    row = conn.execute("SELECT MAX(run_date) FROM fidelity_transactions").fetchone()
    if row and row[0]:
        latest = date.fromisoformat(row[0])
    else:
        latest = date.today()
    return (latest - timedelta(days=_AUTO_SINCE_LOOKBACK_DAYS)).isoformat()


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

    mode = "full" if args.full else "diff"
    since = args.since
    if mode == "diff" and since is None:
        since = _auto_derive_since(conn)
        print(f"  Auto-derived --since={since} (fidelity MAX(run_date) - {_AUTO_SINCE_LOOKBACK_DAYS} days)")

    print(f"  Sync mode: {mode}")

    all_sql: list[str] = []
    total_rows = 0

    for table in TABLES_TO_SYNC:
        if mode == "diff" and table in _DIFF_TABLES:
            sql, count = _dump_table(conn, table, mode="diff")
            print(f"  {table}: {count} rows (INSERT OR IGNORE)")
        elif mode == "diff" and table in _RANGE_TABLES:
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

    # Sync metadata — last_sync timestamp and data coverage
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    last_date_row = conn.execute("SELECT MAX(date) FROM computed_daily").fetchone()
    last_date = last_date_row[0] if last_date_row and last_date_row[0] else ""
    all_sql.append(
        "DELETE FROM sync_meta;\n"
        f"INSERT INTO sync_meta (key, value) VALUES ('last_sync', '{now}');\n"
        f"INSERT INTO sync_meta (key, value) VALUES ('last_date', '{last_date}');"
    )

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

        # shutil.which resolves `npx` to `npx.cmd` on Windows (and the bare
        # binary on macOS/Linux). Passing the resolved full path + arg list
        # avoids shell=True + f-string quoting: the filename is passed
        # directly to CreateProcess / exec.
        npx = shutil.which("npx")
        if npx is None:
            print("Error: `npx` not found in PATH. Install Node.js or add npm bin to PATH.", file=sys.stderr)
            sys.exit(1)
        remote_flag = "--local" if args.local else "--remote"
        result = subprocess.run(
            [npx, "wrangler", "d1", "execute", "portal-db", remote_flag, f"--file={tmp}"],
            cwd=str(_WORKER_DIR),
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            print(f"Error: wrangler failed:\n{result.stderr}", file=sys.stderr)
            sys.exit(1)

        print(result.stdout)
        print("D1 sync complete.")
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
