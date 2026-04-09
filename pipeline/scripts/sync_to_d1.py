"""Sync local timemachine.db tables to Cloudflare D1.

Dumps the 7 tables the Worker needs from the local SQLite database into a SQL
file (DELETE + INSERT), then executes it against D1 via wrangler CLI.

Requires: wrangler CLI authenticated (`wrangler login`)

Usage:
    python scripts/sync_to_d1.py              # sync all tables
    python scripts/sync_to_d1.py --dry-run    # generate SQL but don't execute
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────

_PROJECT_DIR = Path(__file__).resolve().parent.parent
_DB_PATH = _PROJECT_DIR / "data" / "timemachine.db"
_WORKER_DIR = _PROJECT_DIR.parent / "worker"

TABLES_TO_SYNC: list[str] = [
    "computed_daily",
    "computed_prefix",
    "computed_daily_tickers",
    "fidelity_transactions",
    "qianji_transactions",
    "computed_market",
    "computed_holdings_detail",
]


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
    cursor = conn.execute(f"SELECT * FROM {table}")  # noqa: S608
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    lines: list[str] = [f"DELETE FROM {table};"]
    for row in rows:
        values = ", ".join(_escape(v) for v in row)
        lines.append(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values});")

    return "\n".join(lines), len(rows)


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    dry_run = "--dry-run" in sys.argv

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
        sql, count = _dump_table(conn, table)
        all_sql.append(sql)
        total_rows += count
        print(f"  {table}: {count} rows")

    conn.close()

    combined = "\n\n".join(all_sql) + "\n"

    if dry_run:
        print(f"\n[dry-run] Generated {total_rows} total rows, SQL not executed")
        print(f"[dry-run] SQL size: {len(combined):,} bytes")
        return

    # Write to temp file and execute via wrangler
    tmp = Path(tempfile.mktemp(suffix=".sql", prefix="d1_sync_"))
    try:
        tmp.write_text(combined, encoding="utf-8")
        print(f"\nExecuting {total_rows} rows against D1 ({len(combined):,} bytes)...")

        result = subprocess.run(
            ["npx", "wrangler", "d1", "execute", "portal-db", "--remote", f"--file={tmp}"],
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
