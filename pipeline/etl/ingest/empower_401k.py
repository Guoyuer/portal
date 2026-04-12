"""Write Empower 401k snapshots and BUYMF contributions into the timemachine DB.

Parsing and computation (QFX → ``QuarterSnapshot`` / ``Contribution``, daily
interpolation from proxy prices) live in ``etl.empower_401k``
at the package root. This module owns only the DB-write side of that pipeline
so each source's ingest sits next to the rest of the ``ingest/`` subpackage.
"""

from __future__ import annotations

from pathlib import Path

from ..db import get_connection
from ..empower_401k import Contribution, parse_qfx


def ingest_empower_qfx(db_path: Path, qfx_path: Path) -> int:
    """Ingest an Empower 401k QFX file into the database.

    Upserts the snapshot by date; replaces all fund positions for that snapshot.
    Returns the number of funds inserted.
    """
    snap = parse_qfx(qfx_path)
    if not snap.funds:
        return 0

    conn = get_connection(db_path)
    try:
        snap_date = snap.date.isoformat()
        conn.execute("INSERT OR IGNORE INTO empower_snapshots (snapshot_date) VALUES (?)", (snap_date,))
        row = conn.execute("SELECT id FROM empower_snapshots WHERE snapshot_date = ?", (snap_date,)).fetchone()
        snapshot_id: int = row[0]

        conn.execute("DELETE FROM empower_funds WHERE snapshot_id = ?", (snapshot_id,))
        conn.executemany(
            "INSERT INTO empower_funds (snapshot_id, cusip, ticker, shares, price, mktval) VALUES (?, ?, ?, ?, ?, ?)",
            [(snapshot_id, f.cusip, f.ticker, f.shares, f.price, f.mktval) for f in snap.funds],
        )
        conn.commit()
    finally:
        conn.close()

    return len(snap.funds)


def ingest_empower_contributions(db_path: Path, contributions: list[Contribution]) -> int:
    """Upsert 401k contributions (BUYMF) into the database.

    Deduplicates on (date, amount, ticker, cusip).
    Returns number of rows after ingestion.
    """
    if not contributions:
        return 0

    conn = get_connection(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO empower_contributions (date, amount, ticker, cusip)"
            " VALUES (?, ?, ?, ?)",
            [
                (c.date.isoformat(), c.amount, c.ticker, getattr(c, "cusip", ""))
                for c in contributions
            ],
        )
        conn.commit()
        count: int = conn.execute("SELECT COUNT(*) FROM empower_contributions").fetchone()[0]
    finally:
        conn.close()
    return count
