"""Source-agnostic transaction replay.

Takes a standardized ``*_transactions`` table with columns ``(id, txn_date,
action_kind, account, ticker, quantity, amount_usd)`` and accumulates
per-ticker quantity + cost basis as of a given date.

This module has zero knowledge of :class:`SourceKind` or which sources exist.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from etl.sources import ActionKind


@dataclass(frozen=True)
class PositionState:
    """What the cost-basis accumulator yields per ticker at a given as_of date."""
    quantity: float
    cost_basis_usd: float


def replay_transactions(
    db_path: Path,
    table: str,
    as_of: date,
) -> dict[str, PositionState]:
    """Return ``{ticker: PositionState}`` for all tickers with non-zero quantity as of ``as_of``.

    The table is expected to have the standardized columns listed above.
    Source-specific normalizations (CUSIP → ticker, action classification,
    MM-fund routing) happen at ingest time, before rows land in the table.
    """
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        f"SELECT txn_date, action_kind, ticker, quantity, amount_usd "  # noqa: S608 — `table` is trusted (class-level ClassVar)
        f"FROM {table} WHERE txn_date <= ? ORDER BY txn_date, id",
        (as_of.isoformat(),),
    ).fetchall()
    conn.close()

    qty: dict[str, float] = defaultdict(float)
    cost: dict[str, float] = defaultdict(float)

    for _txn_date_str, action, ticker, q, amt in rows:
        if not ticker:
            continue
        kind = ActionKind(action)
        if kind in (ActionKind.BUY, ActionKind.REINVESTMENT):
            cost[ticker] += abs(amt)
            qty[ticker] += q
        elif kind == ActionKind.SELL and qty[ticker] > 0:
            sold_fraction = min(abs(q) / qty[ticker], 1.0)
            cost[ticker] -= cost[ticker] * sold_fraction
            qty[ticker] += q  # q is negative for sells
        # DIVIDEND, WITHDRAWAL, DEPOSIT, TRANSFER, OTHER: no position / cost-basis impact.
        # Cash flow is computed separately by the source that needs it.

    return {
        t: PositionState(quantity=round(qty[t], 6), cost_basis_usd=round(cost[t], 2))
        for t in qty
        if abs(qty[t]) > 1e-3
    }
