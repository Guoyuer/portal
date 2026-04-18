"""Split cross-validation — catches Yahoo/Fidelity disagreements before
pre-split prices are persisted.

Without this check, a silent ``_build_split_factors`` failure or a missing
Fidelity ``DISTRIBUTION`` row would freeze wrong historical prices into
``daily_close`` (INSERT OR IGNORE never retries), producing silently-wrong
historical valuations. Any mismatch raises :class:`SplitValidationError`.
"""
from __future__ import annotations

import sqlite3
from datetime import date

# Absolute share-count tolerance for matching a Yahoo split against a Fidelity
# DISTRIBUTION row. Splits announce integer ratios (2:1, 3:2) and Fidelity
# always rounds to whole shares, so any mismatch beyond a fractional sliver
# is a real discrepancy worth raising.
SPLIT_QTY_TOLERANCE = 0.01


class SplitValidationError(RuntimeError):
    """Raised when Yahoo splits and Fidelity DISTRIBUTION rows don't agree.

    Two directions are checked:

    1. Every Yahoo split that falls inside a holding period must have a
       Fidelity ``DISTRIBUTION`` row with ``qty ≈ pre_split_qty × (ratio - 1)``.
       Failure means Yahoo knows about a split we didn't apply to shares.

    2. Every Fidelity ``DISTRIBUTION`` row with ``qty > 0`` must map to a
       Yahoo split on the same date. Failure means we changed the share
       count but will NOT reverse Yahoo's price adjustment — so pre-split
       dates would be stored at split-adjusted (wrong) prices.

    Either direction failing indicates a data-integrity problem that would
    silently produce wrong historical valuations. Fail loud, fix upstream.
    """


def _validate_splits_against_transactions(
    conn: sqlite3.Connection,
    holding_periods: dict[str, tuple[date, date | None]],
    split_factors: dict[str, list[tuple[date, float]]],
    *,
    today: date | None = None,
) -> None:
    """Two-way cross-check Yahoo splits vs Fidelity DISTRIBUTION rows.

    See :class:`SplitValidationError` for the invariants. ``today`` is
    injected by tests; production callers leave it ``None`` and the
    function uses :meth:`date.today`.
    """
    today = today or date.today()
    mismatches: list[str] = []

    # Direction 1: every Yahoo split inside a holding period must have a
    # matching DISTRIBUTION row with the expected qty delta.
    checked_pairs: set[tuple[str, date]] = set()
    for sym, splits in split_factors.items():
        hp = holding_periods.get(sym)
        if hp is None:
            continue
        hp_start, hp_end_raw = hp
        hp_end = hp_end_raw or today
        for split_date, ratio in splits:
            if split_date <= hp_start or split_date > hp_end:
                continue
            pre_qty = 0.0
            for (qty,) in conn.execute(
                "SELECT quantity FROM fidelity_transactions"
                " WHERE symbol = ?"
                " AND action_kind IN ('buy','sell','reinvestment',"
                "'distribution','redemption','exchange','transfer')"
                " AND run_date < ?",
                (sym, split_date.isoformat()),
            ):
                pre_qty += qty or 0.0
            if pre_qty < SPLIT_QTY_TOLERANCE:
                continue  # not held at split boundary
            actual = 0.0
            for (qty,) in conn.execute(
                "SELECT quantity FROM fidelity_transactions"
                " WHERE symbol = ? AND action_kind = 'distribution'"
                " AND run_date = ?",
                (sym, split_date.isoformat()),
            ):
                actual += qty or 0.0
            expected = pre_qty * (ratio - 1)
            if abs(expected - actual) > SPLIT_QTY_TOLERANCE:
                mismatches.append(
                    f"{sym} {split_date.isoformat()} {ratio}:1 — "
                    f"pre-qty={pre_qty:.4f}, expected DISTRIBUTION qty+={expected:.4f}, "
                    f"got={actual:.4f}"
                )
            checked_pairs.add((sym, split_date))

    # Direction 2: every Fidelity DISTRIBUTION row (qty > 0) must map to a
    # Yahoo split on the same date. Catches silent _build_split_factors
    # failures — without a Yahoo entry, Yahoo's split-adjusted pre-split
    # Close values would be stored un-reversed.
    for sym, run_date, qty in conn.execute(
        "SELECT symbol, run_date, SUM(quantity) FROM fidelity_transactions"
        " WHERE action_kind = 'distribution' AND quantity > 0"
        " GROUP BY symbol, run_date"
    ):
        if not qty or qty <= SPLIT_QTY_TOLERANCE:
            continue
        split_date = date.fromisoformat(run_date)
        if (sym, split_date) in checked_pairs:
            continue  # already validated by direction 1
        mismatches.append(
            f"{sym} {run_date} — Fidelity DISTRIBUTION qty+={qty:.4f} "
            f"but no matching Yahoo split (pre-split price un-adjustment would be skipped)"
        )

    if mismatches:
        msg = (
            "Split cross-validation failed — Yahoo and Fidelity disagree:\n  "
            + "\n  ".join(mismatches)
        )
        raise SplitValidationError(msg)
