"""Source-agnostic transaction replay.

Replays a source table into per-``(account, ticker)`` quantity/cost basis,
plus optional per-account cash, as of a date. Each source supplies one
``ReplayConfig`` describing its table columns and cash knobs.

Position math is intentionally small:
``BUY``/``REINVESTMENT`` add cost + quantity; ``SELL`` removes proportional
cost + quantity; ``REDEMPTION``/``DISTRIBUTION``/``EXCHANGE``/``TRANSFER``
move quantity only; everything else has no position effect. Rows with empty
ticker, zero quantity, or an excluded ticker skip position accumulation.
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from etl.sources._types import ActionKind


@dataclass(frozen=True)
class PositionState:
    """Per-``(account, ticker)`` replay state at a given ``as_of`` date."""
    quantity: float
    cost_basis_usd: float


@dataclass(frozen=True)
class ReplayResult:
    """Output of :func:`replay_transactions`.

    ``positions`` is keyed by ``(account, ticker)``; when the source has
    no account column (Robinhood), the account component is the empty
    string. ``cash`` is populated only when ``config.track_cash=True`` —
    it maps Fidelity-style account numbers to their net cash balance.
    """
    positions: dict[tuple[str, str], PositionState]
    cash: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplayConfig:
    """Per-source schema + replay knobs.

    ``account_col=None`` groups all rows under the empty account string
    (Robinhood). ``exclude_tickers`` skips position/cost-basis accumulation
    while still allowing cash tracking. When ``track_cash=True``,
    ``lot_type_col`` is required; rows with lot type ``Shares`` do not move
    cash. ``mm_drip_tickers`` routes money-market reinvestment quantities
    back into cash.
    """
    table: str
    date_col: str = "txn_date"
    ticker_col: str = "ticker"
    amount_col: str = "amount_usd"
    account_col: str | None = None
    exclude_tickers: frozenset[str] = frozenset()
    track_cash: bool = False
    lot_type_col: str | None = None
    mm_drip_tickers: frozenset[str] = frozenset()


_POSITION_ONLY_KINDS = frozenset({
    ActionKind.REDEMPTION,
    ActionKind.DISTRIBUTION,
    ActionKind.EXCHANGE,
    ActionKind.TRANSFER,
})

_FIDELITY_ACCOUNT_RE = re.compile(r"^[A-Z0-9]+$")


def replay_transactions(
    db_path: Path,
    config: ReplayConfig,
    as_of: date,
) -> ReplayResult:
    """Replay transactions in ``config.table`` up to ``as_of`` inclusive.

    Args:
        db_path: Path to the SQLite database.
        config: Per-source schema + replay knobs.
        as_of: Inclusive replay cutoff date.
    """
    cols: list[str] = [config.date_col, "action_kind", config.ticker_col, "quantity", config.amount_col]
    if config.account_col is not None:
        cols.append(config.account_col)
    if config.track_cash:
        if config.lot_type_col is None:
            msg = "track_cash=True requires lot_type_col to be set"
            raise ValueError(msg)
        cols.append(config.lot_type_col)

    conn = sqlite3.connect(str(db_path))
    try:
        # noqa: S608 — table + column names are trusted (caller-supplied constants).
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {config.table} "
            f"WHERE {config.date_col} <= ? ORDER BY {config.date_col}, id",
            (as_of.isoformat(),),
        ).fetchall()
    finally:
        conn.close()

    qty: dict[tuple[str, str], float] = defaultdict(float)
    cost: dict[tuple[str, str], float] = defaultdict(float)
    cash_flow: dict[str, float] = defaultdict(float)
    mm_drip: dict[str, float] = defaultdict(float)

    for row in rows:
        # Unpack in the order the SELECT emitted (matches `cols`).
        action = row[1]
        ticker = (row[2] or "").strip() if row[2] is not None else ""
        q = row[3] or 0.0
        amt = row[4] or 0.0
        idx = 5
        acct = ""
        if config.account_col is not None:
            acct = (row[idx] or "").strip()
            idx += 1
        lot_type = ""
        if config.track_cash:
            lot_type = (row[idx] or "").strip()

        try:
            kind = ActionKind(action) if action else ActionKind.OTHER
        except ValueError:
            # Unknown action values shouldn't exist — ingest rejects them —
            # but skip gracefully rather than aborting the whole replay.
            kind = ActionKind.OTHER

        key = (acct, ticker)

        # ── Positions (exclude money market + empty/zero-qty rows) ──
        if ticker and ticker not in config.exclude_tickers and q != 0:
            if kind == ActionKind.SELL:
                # Cost-basis reduction must happen before qty is updated.
                if qty[key] > 0:
                    sold_fraction = min(abs(q) / qty[key], 1.0)
                    cost[key] -= cost[key] * sold_fraction
                qty[key] += q
            elif kind in (ActionKind.BUY, ActionKind.REINVESTMENT):
                cost[key] += abs(amt)
                qty[key] += q
            elif kind in _POSITION_ONLY_KINDS:
                # Redemption payouts, stock distributions, exchanges, and
                # share-count transfers move quantity without touching
                # cost basis (matches legacy ``POSITION_PREFIXES``).
                qty[key] += q

        # ── Cash (Fidelity-only; guarded by track_cash) ──
        if config.track_cash and acct and lot_type != "Shares":
            cash_flow[acct] += amt
            if ticker in config.mm_drip_tickers and kind == ActionKind.REINVESTMENT and q != 0:
                mm_drip[acct] += q

    positions = {
        k: PositionState(quantity=round(v, 6), cost_basis_usd=round(cost[k], 2))
        for k, v in qty.items()
        if abs(v) > 0.001
    }

    cash: dict[str, float] = {}
    if config.track_cash:
        cash = {
            acct: round(cash_flow[acct] + mm_drip.get(acct, 0.0), 2)
            for acct in cash_flow
            if _FIDELITY_ACCOUNT_RE.match(acct)
        }

    return ReplayResult(positions=positions, cash=cash)
