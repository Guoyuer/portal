"""Drop write-only ``qianji_transactions.account`` column.

``account`` was populated from Qianji's ``account_from`` field at ingest
but never SELECT'd anywhere — the /cashflow view (via ``v_qianji_txns``)
only exposes date/type/category/amount/is_retirement, and no pipeline
step reads ``account`` back. Retained it previously "for debugging" but
the grep for reads turned up zero hits.

Idempotent: no-op when the column is already gone or on a fresh DB.
Requires SQLite ≥ 3.35 for ``DROP COLUMN`` (released 2021-03).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def migrate(db_path: Path) -> bool:
    """Drop ``qianji_transactions.account`` if present. Returns True if altered."""
    conn = sqlite3.connect(str(db_path))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(qianji_transactions)")}
        if "account" not in cols:
            return False
        conn.execute("ALTER TABLE qianji_transactions DROP COLUMN account")
        conn.commit()
        return True
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    altered = migrate(Path(sys.argv[1]))
    print("altered" if altered else "no-op")
