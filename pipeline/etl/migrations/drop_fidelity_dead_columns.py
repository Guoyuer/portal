"""Drop write-only columns from ``fidelity_transactions``.

``account`` (Fidelity display name; ``account_number`` is the stable
grouping key), ``description`` (CSV narrative text) and ``settlement_date``
(T+N date never read back) were retained as raw CSV values "for debugging"
but no SELECT references them anywhere in the pipeline.

``action`` (the raw CSV string) is INTENTIONALLY kept: it's the input to
the action-kind backfill migration's resync path, which re-classifies
stored rows when ``classify_fidelity_action``'s output changes (long-term
schema-evolution safety net that avoids forcing a full re-ingest every
time an enum widens).

Idempotent: each column is dropped only if present. Fresh DBs built after
this change never have the columns. Requires SQLite ≥ 3.35 for ``DROP COLUMN``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

_DEAD_COLUMNS: tuple[str, ...] = (
    "account",
    "description",
    "settlement_date",
)


def migrate(db_path: Path) -> list[str]:
    """Drop each dead column from fidelity_transactions if present. Returns the dropped names."""
    conn = sqlite3.connect(str(db_path))
    dropped: list[str] = []
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(fidelity_transactions)")}
        for col in _DEAD_COLUMNS:
            if col in cols:
                conn.execute(f"ALTER TABLE fidelity_transactions DROP COLUMN {col}")
                dropped.append(col)
        if dropped:
            conn.commit()
        return dropped
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    dropped = migrate(Path(sys.argv[1]))
    if dropped:
        print("dropped:", ", ".join(dropped))
    else:
        print("no-op")
