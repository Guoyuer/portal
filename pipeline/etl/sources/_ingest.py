"""Shared ingest primitives used by every broker source.

Currently a single helper: :func:`range_replace_insert`. Both Fidelity and
Robinhood CSVs are authoritative within their own date ranges; the ingest
pattern is "DELETE everything in ``[min_date, max_date]``, INSERT everything
that was parsed from the file." Centralising keeps the two call sites in
lockstep + spares every source from repeating the empty-rows guard.
"""
from __future__ import annotations

import sqlite3
from typing import Any


def range_replace_insert(
    conn: sqlite3.Connection,
    table: str,
    date_col: str,
    rows: list[tuple[Any, ...]],
    date_idx: int,
    insert_sql: str,
) -> None:
    """DELETE rows whose ``date_col`` is in ``rows``' min/max range, then INSERT.

    Args:
        conn: Open SQLite connection; caller owns commit.
        table: Target table name (trusted — not parameterized).
        date_col: Name of the date column to bound by.
        rows: Parsed input rows, each a tuple with the date at position
            ``date_idx``.
        date_idx: Position of the date in each tuple.
        insert_sql: Full ``INSERT INTO ... VALUES (?, ?, ...)`` statement
            with placeholders matching ``rows``' shape.

    No-op when ``rows`` is empty — no DELETE, no INSERT. Caller is
    responsible for committing the transaction.
    """
    if not rows:
        return
    dates = [r[date_idx] for r in rows]
    conn.execute(
        f"DELETE FROM {table} WHERE {date_col} BETWEEN ? AND ?",  # noqa: S608 — trusted args
        (min(dates), max(dates)),
    )
    conn.executemany(insert_sql, rows)
