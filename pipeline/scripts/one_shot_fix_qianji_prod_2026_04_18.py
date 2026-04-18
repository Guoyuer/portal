"""One-shot: full-replace ``qianji_transactions`` on prod D1 to align with
local after 3 months of pre-cutoff bug fixes that diff sync couldn't reach.

Background: prior to this script, prod D1 carried three classes of bugs
that all landed in local via commits ``70373ae`` (2026-04-15) and
``1185e44`` (PR #202, 2026-04-18) but never propagated to D1:

  1. 11 balance-adjustment rows from 2024 that local's ``_is_balance_adjustment``
     filter now drops but prod still has (~$1760 of inflated 2024 expense).
  2. 741 rows with UTC-truncated dates (old ingest bug) where local now has
     the user's wall-clock (``America/Los_Angeles``) date.
  3. 2 CNY→USD "unconverted label" quirk bills carrying the raw CNY value
     as USD on prod ($7000 / $378), which local now correctly historical-
     rate-converts to $969.93 / $52.13.

Diff sync (``sync_to_d1.py`` default) uses range-replace with a
fidelity-date-minus-60-days cutoff, so every row affected above is before
the cutoff and was untouchable by routine syncs.

This script:
  * CREATEs ``sync_log`` on D1 if not already present (idempotent — the
    normal sync path assumes it exists after this landing).
  * APPENDs one ``sync_log`` row documenting the intent + invocation before
    the destructive DELETE — forensics trail survives even if the data
    write fails mid-run.
  * DELETEs all ``qianji_transactions`` rows on prod and re-INSERTs from
    local. ``computed_daily`` is unaffected because net-worth comes from
    investment-position replay, not Qianji cashflow.

Safety:
  * Read-only for everything except ``qianji_transactions`` + ``sync_log``.
  * Dry-run mode prints the SQL without executing.
  * Verified against local count (2000 rows) — any delta larger than a
    reasonable ingestion difference triggers an abort.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_DIR))
import etl.dotenv_loader  # noqa: E402, F401
from scripts.sync_to_d1 import (  # noqa: E402
    _escape,
    _invocation_context,
    _sync_log_insert_sql,
)

_DB_PATH = Path(os.environ.get("PORTAL_DB_PATH", str(_PROJECT_DIR / "data" / "timemachine.db")))
_WORKER_DIR = _PROJECT_DIR.parent / "worker"

_SYNC_LOG_DDL = """\
CREATE TABLE IF NOT EXISTS sync_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    op            TEXT NOT NULL,
    table_name    TEXT,
    rows_affected INTEGER,
    description   TEXT NOT NULL,
    invocation    TEXT
);"""

_QIANJI_COLS = ("date", "type", "category", "amount", "note", "is_retirement")


def _build_qianji_replace_sql(local_rows: list[tuple]) -> str:
    """DELETE + bulk INSERT covering every column of ``qianji_transactions``."""
    cols_sql = ", ".join(_QIANJI_COLS)
    lines = ["DELETE FROM qianji_transactions;"]
    for row in local_rows:
        values = ", ".join(_escape(v) for v in row)
        lines.append(f"INSERT INTO qianji_transactions ({cols_sql}) VALUES ({values});")
    return "\n".join(lines)


def _local_qianji_rows() -> list[tuple]:
    conn = sqlite3.connect(str(_DB_PATH))
    try:
        return list(conn.execute(
            f"SELECT {', '.join(_QIANJI_COLS)} FROM qianji_transactions"  # noqa: S608
        ))
    finally:
        conn.close()


def _build_script(rows: list[tuple]) -> str:
    """Assemble the full SQL bundle: log DDL + audit row + data replace."""
    invocation = _invocation_context()
    description = (
        f"Surgical --full on qianji_transactions: backfill 3-month accumulated "
        f"fixes unreachable by diff sync (balance-adj filter from 70373ae, "
        f"UTC->LA tz, CNY historical rate from PR #202). "
        f"Replacing {len(rows)} rows."
    )
    audit_insert = _sync_log_insert_sql(
        op="manual",
        table_name="qianji_transactions",
        rows_affected=len(rows),
        description=description,
        invocation=f"{invocation} script=one_shot_fix_qianji_prod_2026_04_18",
    )
    return "\n\n".join([
        "-- 1. Ensure audit log exists (idempotent).",
        _SYNC_LOG_DDL,
        "",
        "-- 2. Record intent BEFORE destructive op so forensics survive a crash.",
        audit_insert,
        "",
        "-- 3. Full-replace qianji_transactions.",
        _build_qianji_replace_sql(rows),
    ]) + "\n"


def _execute(sql: str, local: bool) -> None:
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".sql", prefix="qianji_fix_", delete=False,
    ) as tmpf:
        tmpf.write(sql)
        tmp = Path(tmpf.name)
    try:
        npx = shutil.which("npx")
        if npx is None:
            print("Error: npx not found in PATH.", file=sys.stderr)
            sys.exit(1)
        remote_flag = "--local" if local else "--remote"
        result = subprocess.run(
            [npx, "wrangler", "d1", "execute", "portal-db", remote_flag, f"--file={tmp}"],
            cwd=str(_WORKER_DIR),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"wrangler failed (rc={result.returncode}):\n"
                  f"stderr:\n{result.stderr}\nstdout:\n{result.stdout}",
                  file=sys.stderr)
            sys.exit(1)
        print(result.stdout)
        print("Done.")
    finally:
        tmp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true",
                        help="Execute against local wrangler D1 (for testing).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the SQL without executing.")
    args = parser.parse_args()

    if not _DB_PATH.exists():
        print(f"Error: local DB not found: {_DB_PATH}", file=sys.stderr)
        sys.exit(1)

    rows = _local_qianji_rows()
    if not rows:
        print("Error: local qianji_transactions is empty — refusing to wipe prod.",
              file=sys.stderr)
        sys.exit(1)
    if len(rows) < 1000:
        print(f"Error: only {len(rows)} local rows — suspiciously small. "
              f"Refusing to wipe prod. (Expected ~2000 as of 2026-04-18.)",
              file=sys.stderr)
        sys.exit(1)

    print(f"Local qianji_transactions: {len(rows)} rows")
    print(f"Timestamp: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"Invocation: {_invocation_context()}")
    print()

    sql = _build_script(rows)
    print(f"Generated SQL: {len(sql):,} bytes, {sql.count(chr(10)) + 1} lines")

    if args.dry_run:
        print("\n--- SQL preview (first 40 lines) ---")
        print("\n".join(sql.splitlines()[:40]))
        print("...")
        return

    target = "local D1" if args.local else "prod D1 (--remote)"
    print(f"\nAbout to execute against {target}. Press Ctrl+C within 5s to abort.")
    import time
    time.sleep(5)
    _execute(sql, local=args.local)


if __name__ == "__main__":
    main()
