"""Drop ``qianji_transactions.source_id`` from legacy DBs.

Context: ``source_id`` was introduced briefly to give the changelog snapshot
a stable identity across runs, because the live-rate CNY→USD conversion
path in :func:`etl.ingest.qianji_db.parse_qj_amount` caused the USD ``amount``
of quirky bills to drift every run. The drift has since been fixed at its
root — ``parse_qj_amount`` now uses per-bill-date historical rates from
``daily_close WHERE symbol='CNY=X'`` — so the snapshot can go back to
keying on the ``(date, type, category, amount)`` content tuple without
surfacing ghost additions, and ``source_id`` has no remaining consumer.

This migration drops the column from any local DB where the earlier
``add_qianji_source_id`` migration had already added it. Idempotent: fresh
DBs never had the column; re-running finds it gone and no-ops.

Requires SQLite ≥ 3.35 for ``DROP COLUMN`` (released 2021-03).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def migrate(db_path: Path) -> bool:
    """Drop ``qianji_transactions.source_id`` if present. Returns True if altered."""
    conn = sqlite3.connect(str(db_path))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(qianji_transactions)")}
        if "source_id" not in cols:
            return False
        conn.execute("ALTER TABLE qianji_transactions DROP COLUMN source_id")
        conn.commit()
        return True
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    altered = migrate(Path(sys.argv[1]))
    print("altered" if altered else "no-op")
