"""One-shot backfill: populate ``fidelity_transactions.action_kind`` from ``action``.

Idempotent on both axes:
  * Adds the ``action_kind`` column only if it's missing from an existing DB
    (fresh DBs already have it via :mod:`etl.db`'s ``CREATE TABLE IF NOT EXISTS``).
  * Only populates rows where ``action_kind IS NULL`` — re-runs after ingest
    no-op on already-classified rows.

Invoked once from :mod:`pipeline.scripts.build_timemachine_db` after the
Fidelity CSV ingest step, which is the earliest point every new/updated row
is available for backfill.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from etl.sources.fidelity import classify_fidelity_action


def migrate(db_path: Path) -> int:
    """Backfill ``action_kind`` for rows that don't have one. Returns rows touched."""
    conn = sqlite3.connect(str(db_path))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(fidelity_transactions)")}
        if "action_kind" not in cols:
            conn.execute("ALTER TABLE fidelity_transactions ADD COLUMN action_kind TEXT")

        rows = conn.execute(
            "SELECT id, action FROM fidelity_transactions WHERE action_kind IS NULL"
        ).fetchall()
        for row_id, action in rows:
            conn.execute(
                "UPDATE fidelity_transactions SET action_kind = ? WHERE id = ?",
                (classify_fidelity_action(action or "").value, row_id),
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    touched = migrate(Path(sys.argv[1]))
    print(f"backfilled {touched} rows")
