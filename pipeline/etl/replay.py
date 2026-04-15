"""Source-agnostic transaction replay.

Accumulates per-``(account, ticker)`` quantity + cost basis (and, when
enabled, per-account cash) from a standardized transactions table as of
a given ``as_of`` date. Used by every source that persists its own
transactions — Fidelity and Robinhood today.

The primitive is schema-aware via column-name parameters so it can point
at tables with different layouts (``fidelity_transactions`` uses
``run_date`` / ``account_number`` / ``symbol`` / ``amount``; Robinhood
uses ``txn_date`` / ``ticker`` / ``amount_usd`` with no account column).

Supported :class:`~etl.sources.ActionKind` vocabulary:

  - ``BUY`` / ``REINVESTMENT`` — ``cost += abs(amt); qty += q``
  - ``SELL`` — ``cost -= cost × sold_fraction`` (only when qty > 0),
    then ``qty += q`` (q is negative).
  - ``REDEMPTION`` / ``DISTRIBUTION`` / ``EXCHANGE`` / ``TRANSFER`` —
    qty-only (``qty += q``); no cost-basis impact. Matches the legacy
    Fidelity ``POSITION_PREFIXES`` behaviour (``REDEMPTION PAYOUT``,
    ``TRANSFERRED FROM/TO``, ``DISTRIBUTION``, ``EXCHANGED TO``).
  - ``DIVIDEND`` / ``DEPOSIT`` / ``WITHDRAWAL`` / ``OTHER`` — no position
    effect. These may still move cash when cash tracking is enabled.

Rows are filtered out of the position accumulator when the ticker is
empty, the quantity is zero, or the ticker is in ``exclude_tickers``
(used to exclude Fidelity's money-market funds from share accumulation
while still letting them flow through the cash ledger).
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from etl.sources import ActionKind


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
    string. ``cash`` is populated only when ``track_cash=True`` — it maps
    Fidelity-style account numbers to their net cash balance.
    """
    positions: dict[tuple[str, str], PositionState]
    cash: dict[str, float] = field(default_factory=dict)


_POSITION_ONLY_KINDS = frozenset({
    ActionKind.REDEMPTION,
    ActionKind.DISTRIBUTION,
    ActionKind.EXCHANGE,
    ActionKind.TRANSFER,
})

_FIDELITY_ACCOUNT_RE = re.compile(r"^[A-Z0-9]+$")


def replay_transactions(
    db_path: Path,
    table: str,
    as_of: date,
    *,
    date_col: str = "txn_date",
    ticker_col: str = "ticker",
    amount_col: str = "amount_usd",
    account_col: str | None = None,
    exclude_tickers: frozenset[str] | None = None,
    track_cash: bool = False,
    lot_type_col: str | None = None,
    cash_exclude_lot_type: str = "Shares",
    mm_drip_tickers: frozenset[str] | None = None,
) -> ReplayResult:
    """Replay transactions in ``table`` up to ``as_of`` inclusive.

    Args:
        db_path: Path to the SQLite database.
        table: Fully-qualified transaction table name.
        as_of: Inclusive replay cutoff date.
        date_col: Name of the ISO-date column (defaults to ``txn_date``).
        ticker_col: Name of the ticker / symbol column.
        amount_col: Name of the cash-delta column.
        account_col: Name of the per-account grouping column. When
            ``None`` (e.g. Robinhood), every row is accumulated under the
            empty account string.
        exclude_tickers: Tickers to skip when applying position / cost-
            basis updates. These rows can still flow through the cash
            ledger (Fidelity's money-market funds).
        track_cash: When ``True``, accumulate per-account cash from every
            row that isn't tagged ``cash_exclude_lot_type``. Fidelity-
            only today; Robinhood leaves this disabled.
        lot_type_col: Column carrying the Fidelity lot-type marker
            (``Cash`` / ``Margin`` / ``Shares`` / ``Financing``).
            Required when ``track_cash=True``.
        cash_exclude_lot_type: Lot-type value whose rows don't
            participate in cash accumulation — ``Shares`` means stock
            distributions, lending, and MM sweeps don't double-count.
        mm_drip_tickers: MM-fund tickers whose ``REINVESTMENT`` rows
            fold share-count deltas back into the cash ledger (match
            legacy ``mm_drip`` tallying).
    """
    cols: list[str] = [date_col, "action_kind", ticker_col, "quantity", amount_col]
    if account_col is not None:
        cols.append(account_col)
    if track_cash:
        if lot_type_col is None:
            msg = "track_cash=True requires lot_type_col to be set"
            raise ValueError(msg)
        cols.append(lot_type_col)

    conn = sqlite3.connect(str(db_path))
    try:
        # noqa: S608 — table + column names are trusted (caller-supplied constants).
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} "
            f"WHERE {date_col} <= ? ORDER BY {date_col}, id",
            (as_of.isoformat(),),
        ).fetchall()
    finally:
        conn.close()

    qty: dict[tuple[str, str], float] = defaultdict(float)
    cost: dict[tuple[str, str], float] = defaultdict(float)
    cash_flow: dict[str, float] = defaultdict(float)
    mm_drip: dict[str, float] = defaultdict(float)

    excludes = exclude_tickers or frozenset()
    mm_dripset = mm_drip_tickers or frozenset()

    for row in rows:
        # Unpack in the order the SELECT emitted (matches `cols`).
        _txn_date = row[0]
        action = row[1]
        ticker = (row[2] or "").strip() if row[2] is not None else ""
        q = row[3] or 0.0
        amt = row[4] or 0.0
        idx = 5
        acct = ""
        if account_col is not None:
            acct = (row[idx] or "").strip()
            idx += 1
        lot_type = ""
        if track_cash:
            lot_type = (row[idx] or "").strip()
            idx += 1

        try:
            kind = ActionKind(action)
        except ValueError:
            # Unknown action values shouldn't exist — ingest rejects them —
            # but skip gracefully rather than aborting the whole replay.
            kind = ActionKind.OTHER

        key = (acct, ticker)

        # ── Positions (exclude money market + empty/zero-qty rows) ──
        if ticker and ticker not in excludes and q != 0:
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
        if track_cash and acct and lot_type != cash_exclude_lot_type:
            cash_flow[acct] += amt
            if ticker in mm_dripset and kind == ActionKind.REINVESTMENT and q != 0:
                mm_drip[acct] += q

    positions = {
        k: PositionState(quantity=round(v, 6), cost_basis_usd=round(cost[k], 2))
        for k, v in qty.items()
        if abs(v) > 0.001
    }

    cash: dict[str, float] = {}
    if track_cash:
        cash = {
            acct: round(cash_flow[acct] + mm_drip.get(acct, 0.0), 2)
            for acct in cash_flow
            if _FIDELITY_ACCOUNT_RE.match(acct)
        }

    return ReplayResult(positions=positions, cash=cash)
