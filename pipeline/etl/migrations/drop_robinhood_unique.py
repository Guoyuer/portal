"""One-shot: drop the UNIQUE(txn_date, ticker, action, quantity, amount_usd)
constraint from ``robinhood_transactions``.

The original Task 17 schema carried that UNIQUE constraint to guarantee
idempotent re-ingest, but Robinhood CSVs legitimately contain duplicate
rows (e.g. two recurring buys with identical date/qty/amount represent two
physical trades, not one). Collapsing them broke L1 parity with the legacy
``replay_robinhood`` path. Task 19 switches :meth:`RobinhoodSource.ingest`
to Fidelity's range-replace pattern (DELETE within CSV's date range +
INSERT everything), which gives the same idempotency guarantee without
discarding real data.

SQLite lacks ``ALTER TABLE DROP CONSTRAINT``, so we take the standard
"rename + recreate" dance — preserving whatever rows a prior Task-18 build
may have already inserted.

Idempotent: a no-op if the UNIQUE is already absent (fresh DBs built by
:func:`etl.db.init_db` after Task 19 never had it).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def _has_unique_constraint(conn: sqlite3.Connection) -> bool:
    """True iff ``robinhood_transactions`` carries any UNIQUE index (implicit
    or explicit). A fresh Task-19 table has only the PRIMARY KEY implicit
    index ``sqlite_autoindex_robinhood_transactions_1`` — which is *not*
    flagged as UNIQUE in :pragma:`index_list`."""
    rows = conn.execute("PRAGMA index_list(robinhood_transactions)").fetchall()
    # Columns of PRAGMA index_list: (seq, name, unique, origin, partial)
    # origin='u' → explicit UNIQUE constraint; 'pk' → PRIMARY KEY (ignore).
    return any(r[2] == 1 and r[3] == "u" for r in rows)


def migrate(db_path: Path) -> bool:
    """Drop the UNIQUE constraint via recreate-and-copy. Returns True iff a
    rewrite actually happened."""
    if not db_path.exists():
        return False
    conn = sqlite3.connect(str(db_path))
    try:
        # Table may not exist yet (e.g. ``init_db`` hasn't run on this DB).
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='robinhood_transactions'"
        ).fetchone()
        if exists is None:
            return False
        if not _has_unique_constraint(conn):
            return False

        # Rename → create new without UNIQUE → copy rows → drop old.
        conn.executescript(
            """
            ALTER TABLE robinhood_transactions RENAME TO _robinhood_transactions_legacy;
            CREATE TABLE robinhood_transactions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                txn_date        TEXT NOT NULL,
                action          TEXT NOT NULL DEFAULT '',
                action_kind     TEXT NOT NULL,
                ticker          TEXT NOT NULL DEFAULT '',
                quantity        REAL NOT NULL DEFAULT 0,
                amount_usd      REAL NOT NULL DEFAULT 0,
                raw_description TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO robinhood_transactions
                (id, txn_date, action, action_kind, ticker, quantity, amount_usd, raw_description)
            SELECT
                id, txn_date, action, action_kind, ticker, quantity, amount_usd, raw_description
            FROM _robinhood_transactions_legacy;
            DROP TABLE _robinhood_transactions_legacy;
            """
        )
        conn.commit()
        return True
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    changed = migrate(Path(sys.argv[1]))
    print("rewrote robinhood_transactions" if changed else "no migration needed")
