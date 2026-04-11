"""Sync local timemachine.db tables to Cloudflare D1.

Dumps the 7 tables the Worker needs from the local SQLite database into a SQL
file (DELETE + INSERT), then executes it against D1 via wrangler CLI.

Requires: wrangler CLI authenticated (`wrangler login`)

Usage:
    python scripts/sync_to_d1.py                      # full sync to remote D1
    python scripts/sync_to_d1.py --local               # full sync to local D1
    python scripts/sync_to_d1.py --dry-run              # generate SQL but don't execute
    python scripts/sync_to_d1.py --diff                 # diff sync: INSERT OR IGNORE for append-only tables
    python scripts/sync_to_d1.py --diff --since 2025-01-01  # diff sync with range-replace cutoff
"""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────

_PROJECT_DIR = Path(__file__).resolve().parent.parent
_DB_PATH = _PROJECT_DIR / "data" / "timemachine.db"
_WORKER_DIR = _PROJECT_DIR.parent / "worker"

TABLES_TO_SYNC: list[str] = [
    "computed_daily",
    "computed_daily_tickers",
    "fidelity_transactions",
    "qianji_transactions",
    "computed_market_indices",
    "computed_market_indicators",
    "computed_holdings_detail",
    "econ_series",
]

# Column subsets to sync to D1.  None → all columns (SELECT *).
_D1_COLUMNS: dict[str, list[str] | None] = {
    "fidelity_transactions": ["run_date", "action_type", "symbol", "amount"],
    "qianji_transactions": ["date", "type", "category", "amount"],
}

# Tables that use INSERT OR IGNORE in diff mode (append-only, have date PK)
_DIFF_TABLES: set[str] = {"computed_daily", "computed_daily_tickers"}

# Tables that use range-replace in diff mode (delete after cutoff, reinsert).
# Value is a SQL expression that yields a YYYY-MM-DD–sortable string for date comparison.
_RANGE_TABLES: dict[str, str] = {
    "fidelity_transactions": "substr(run_date,7,4)||substr(run_date,1,2)||substr(run_date,4,2)",
    "qianji_transactions": "date",
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


def _dump_table(conn: sqlite3.Connection, table: str) -> tuple[str, int]:
    """Generate DELETE + INSERT statements for one table. Returns (sql, row_count)."""
    cols = _D1_COLUMNS.get(table)
    if cols:
        col_list = ", ".join(cols)
        cursor = conn.execute(f"SELECT {col_list} FROM {table}")  # noqa: S608
    else:
        cursor = conn.execute(f"SELECT * FROM {table}")  # noqa: S608
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    lines: list[str] = [f"DELETE FROM {table};"]
    for row in rows:
        values = ", ".join(_escape(v) for v in row)
        lines.append(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values});")

    return "\n".join(lines), len(rows)


def _dump_table_diff(conn: sqlite3.Connection, table: str) -> tuple[str, int]:
    """Generate INSERT OR IGNORE statements (no DELETE). For append-only tables with a date PK."""
    cols = _D1_COLUMNS.get(table)
    if cols:
        col_list = ", ".join(cols)
        cursor = conn.execute(f"SELECT {col_list} FROM {table}")  # noqa: S608
    else:
        cursor = conn.execute(f"SELECT * FROM {table}")  # noqa: S608
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    lines: list[str] = []
    for row in rows:
        values = ", ".join(_escape(v) for v in row)
        lines.append(f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) VALUES ({values});")

    return "\n".join(lines), len(rows)


def _dump_table_range(conn: sqlite3.Connection, table: str, date_expr: str, since: str) -> tuple[str, int]:
    """Delete rows after cutoff date, then INSERT new rows. For range-replace tables."""
    cols = _D1_COLUMNS.get(table)
    if cols:
        col_list = ", ".join(cols)
        cursor = conn.execute(f"SELECT {col_list} FROM {table} WHERE {date_expr} > ?", (since,))  # noqa: S608
    else:
        cursor = conn.execute(f"SELECT * FROM {table} WHERE {date_expr} > ?", (since,))  # noqa: S608
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    lines: list[str] = [f"DELETE FROM {table} WHERE {date_expr} > '{since}';"]
    for row in rows:
        values = ", ".join(_escape(v) for v in row)
        lines.append(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values});")

    return "\n".join(lines), len(rows)


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync timemachine.db tables to Cloudflare D1")
    parser.add_argument("--local", action="store_true", help="Sync to local D1 (wrangler dev)")
    parser.add_argument("--dry-run", action="store_true", help="Generate SQL but don't execute")
    parser.add_argument("--diff", action="store_true", help="Diff sync: only new rows for computed tables")
    parser.add_argument("--since", type=str, default=None, help="Cutoff date for range-replace tables (YYYY-MM-DD)")
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

    all_sql: list[str] = []
    total_rows = 0

    for table in TABLES_TO_SYNC:
        if args.diff and table in _DIFF_TABLES:
            sql, count = _dump_table_diff(conn, table)
            print(f"  {table}: {count} rows (diff: INSERT OR IGNORE)")
        elif args.diff and table in _RANGE_TABLES:
            if not args.since:
                print("Error: --diff requires --since for range-replace tables", file=sys.stderr)
                sys.exit(1)
            sql, count = _dump_table_range(conn, table, _RANGE_TABLES[table], args.since)
            print(f"  {table}: {count} rows (range-replace since {args.since})")
        else:
            sql, count = _dump_table(conn, table)
            print(f"  {table}: {count} rows")
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

    conn.close()

    combined = "\n\n".join(all_sql) + "\n"

    if args.dry_run:
        print(f"\n[dry-run] Generated {total_rows} total rows, SQL not executed")
        print(f"[dry-run] SQL size: {len(combined):,} bytes")
        return

    # Write to temp file and execute via wrangler
    tmp = Path(tempfile.mktemp(suffix=".sql", prefix="d1_sync_"))
    try:
        tmp.write_text(combined, encoding="utf-8")
        print(f"\nExecuting {total_rows} rows against D1 ({len(combined):,} bytes)...")

        # Use shell=True on Windows so npx.cmd is found
        remote_flag = "--local" if args.local else "--remote"
        cmd = f'npx wrangler d1 execute portal-db {remote_flag} --file="{tmp}"'
        result = subprocess.run(
            cmd,
            cwd=str(_WORKER_DIR),
            capture_output=True,
            text=True,
            shell=True,
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
