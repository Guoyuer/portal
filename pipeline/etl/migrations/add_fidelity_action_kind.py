"""Backfill + resync ``fidelity_transactions.action_kind`` from ``action``.

Idempotent on three axes:
  * Adds the ``action_kind`` column only if it's missing from an existing DB
    (fresh DBs already have it via :mod:`etl.db`'s ``CREATE TABLE IF NOT EXISTS``).
  * Populates rows where ``action_kind IS NULL``.
  * Resyncs rows where ``action_kind`` disagrees with the current classifier —
    catches the case where :func:`classify_fidelity_action`'s mapping has
    widened (e.g. ``REDEMPTION`` / ``DISTRIBUTION`` / ``EXCHANGE`` moved
    from ``OTHER`` to their own ``ActionKind``) without re-ingesting every
    CSV. Without this, older rows out of the current CSVs' date ranges
    would stay stale (``range_replace_insert`` only touches rows inside
    the new CSV's window).

Invoked once from :mod:`pipeline.scripts.build_timemachine_db` after the
Fidelity CSV ingest step, which is the earliest point every new/updated
row is available for classification.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from etl.sources.fidelity import classify_fidelity_action


def migrate(db_path: Path) -> int:
    """Backfill or resync ``action_kind`` with the current classifier.

    Returns the number of rows whose ``action_kind`` was written.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(fidelity_transactions)")}
        if "action_kind" not in cols:
            conn.execute("ALTER TABLE fidelity_transactions ADD COLUMN action_kind TEXT")

        rows = conn.execute(
            "SELECT id, action, action_kind FROM fidelity_transactions"
        ).fetchall()
        touched = 0
        for row_id, action, current in rows:
            expected = classify_fidelity_action(action or "").value
            if current != expected:
                conn.execute(
                    "UPDATE fidelity_transactions SET action_kind = ? WHERE id = ?",
                    (expected, row_id),
                )
                touched += 1
        conn.commit()
        return touched
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    touched = migrate(Path(sys.argv[1]))
    print(f"backfilled {touched} rows")
