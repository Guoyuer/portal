"""Property-based tests for :func:`etl.replay.replay_transactions`.

Complements the example-based tests in ``test_replay_primitive.py`` by
generating random transaction sequences via ``hypothesis`` and asserting
universal invariants of the replay state machine.

Tiered per docs/TODO.md T3 — focused on the invariants most likely to
surface regressions when ``replay.py`` is modified (sign convention,
non-negative cost basis, and split/reinvestment qty conservation — the
trickiest code path because DISTRIBUTION is qty-only while REINVESTMENT
adds to cost).
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from etl.replay import ReplayConfig, replay_transactions
from etl.sources import ActionKind
from tests.unit._replay_fixtures import insert_prop_rows

# A small ticker pool keeps the state space tight — we want invariants to
# exercise the aggregation logic across shared keys, not explode into a
# thousand single-row positions.
_TICKERS = ["FOO", "BAR", "BAZ", "QUX"]

# Robinhood-shaped config (no account column, no cash tracking) — matches
# the table schema set up by ``_make_db``.
_PROPERTY_REPLAY = ReplayConfig(table="prop_transactions")


def _make_db(tmp_path: Path) -> Path:
    """Create an empty transactions table with the Robinhood-shaped schema.

    A fresh filename per call — hypothesis re-enters the same test function
    (and the same ``tmp_path``) once per generated example, so a stable name
    would collide on the second example.
    """
    db = tmp_path / f"prop-{uuid.uuid4().hex}.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE prop_transactions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            txn_date     TEXT NOT NULL,
            action_kind  TEXT NOT NULL,
            ticker       TEXT NOT NULL,
            quantity     REAL NOT NULL,
            amount_usd   REAL NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()
    return db


# ── Strategies ──────────────────────────────────────────────────────────────


@st.composite
def _buy_sell_sequence(
    draw: st.DrawFn,
    min_size: int = 1,
    max_size: int = 20,
) -> list[tuple[str, str, str, float, float]]:
    """Generate a monotonic-date sequence of BUY / SELL rows with correct signs.

    Sells are only emitted for tickers that have positive accumulated quantity
    so far — a well-formed sequence shouldn't overdraw a position. This keeps
    the generated scenarios inside the domain where non-negative cost basis
    should hold universally.
    """
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    rows: list[tuple[str, str, str, float, float]] = []
    qty_by_ticker: dict[str, float] = dict.fromkeys(_TICKERS, 0.0)
    start = date(2024, 1, 1)

    for i in range(n):
        ticker = draw(st.sampled_from(_TICKERS))
        # Decide BUY vs SELL based on whether we have shares to sell.
        can_sell = qty_by_ticker[ticker] > 0.01
        if can_sell:
            action = draw(st.sampled_from([ActionKind.BUY, ActionKind.SELL]))
        else:
            action = ActionKind.BUY

        if action == ActionKind.BUY:
            qty = draw(st.floats(min_value=0.1, max_value=1000.0, allow_nan=False))
            # BUY: money out → negative amount in our sign convention; replay
            # uses abs() so the sign doesn't matter for cost — but we mimic
            # real CSVs for fidelity with the callers' contracts.
            price = draw(st.floats(min_value=1.0, max_value=500.0, allow_nan=False))
            amount = -qty * price
            qty_by_ticker[ticker] += qty
            rows.append((
                (start + timedelta(days=i)).isoformat(),
                ActionKind.BUY.value,
                ticker,
                qty,
                amount,
            ))
        else:
            # SELL at most what we own; qty is negative by convention.
            sell_qty = draw(st.floats(
                min_value=0.01, max_value=qty_by_ticker[ticker], allow_nan=False,
            ))
            price = draw(st.floats(min_value=1.0, max_value=500.0, allow_nan=False))
            amount = sell_qty * price  # money in → positive
            qty_by_ticker[ticker] -= sell_qty
            rows.append((
                (start + timedelta(days=i)).isoformat(),
                ActionKind.SELL.value,
                ticker,
                -sell_qty,
                amount,
            ))
    return rows


# ── Property 1: Non-negative cost basis + sign conventions ──────────────────


@given(rows=_buy_sell_sequence())
@settings(max_examples=75, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_replay_cost_basis_is_non_negative(tmp_path: Path, rows: list[tuple[str, str, str, float, float]]) -> None:
    """For a well-formed BUY/SELL sequence (no overdrafts), every resulting
    position must have ``cost_basis_usd >= 0``.

    The SELL branch reduces cost proportionally — ``cost -= cost * sold_fraction``
    with ``sold_fraction`` clamped to ``[0, 1]``. So the invariant hinges on
    that clamp: if sold_fraction could ever exceed 1.0 (over-sell), cost
    would cross zero. Generated sequences only sell what was bought, so the
    clamp shouldn't fire — any violation indicates a real bug in the
    state-machine arithmetic.
    """
    db = _make_db(tmp_path)
    insert_prop_rows(db, rows)
    result = replay_transactions(db, _PROPERTY_REPLAY, date(2099, 12, 31))

    for key, state in result.positions.items():
        assert state.cost_basis_usd >= 0, (
            f"negative cost_basis_usd for {key}: {state.cost_basis_usd} "
            f"(quantity={state.quantity}, rows={rows})"
        )


# ── Property 2: Quantity sum invariant for BUY-only sequences ───────────────


@given(
    buys=st.lists(
        st.tuples(
            st.sampled_from(_TICKERS),
            st.floats(min_value=0.1, max_value=1000.0, allow_nan=False),
            st.floats(min_value=1.0, max_value=10000.0, allow_nan=False),
        ),
        min_size=1,
        max_size=15,
    ),
)
@settings(max_examples=75, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_replay_buy_only_conserves_quantity_and_cost(
    tmp_path: Path,
    buys: list[tuple[str, float, float]],
) -> None:
    """A sequence of BUY-only rows must yield ``quantity == sum(buy_qty)`` and
    ``cost_basis_usd == sum(buy_amt)`` per ticker, modulo the round-to-6 /
    round-to-2 the replay applies on exit.

    This is the simplest algebraic invariant of the state machine — any
    drift here means the BUY branch dropped or double-counted a row.
    """
    db = _make_db(tmp_path)
    rows: list[tuple[str, str, str, float, float]] = []
    expected_qty: dict[str, float] = {}
    expected_cost: dict[str, float] = {}
    start = date(2024, 1, 1)

    for i, (ticker, qty, amount) in enumerate(buys):
        rows.append((
            (start + timedelta(days=i)).isoformat(),
            ActionKind.BUY.value,
            ticker,
            qty,
            -amount,
        ))
        expected_qty[ticker] = expected_qty.get(ticker, 0.0) + qty
        expected_cost[ticker] = expected_cost.get(ticker, 0.0) + amount

    insert_prop_rows(db, rows)
    result = replay_transactions(db, _PROPERTY_REPLAY, date(2099, 12, 31))

    for ticker, exp_qty in expected_qty.items():
        # Replay drops positions with |qty| <= 0.001; our generator's minimum
        # qty is 0.1, and we never sell, so every ticker should survive.
        state = result.positions[("", ticker)]
        assert state.quantity == pytest.approx(exp_qty, rel=1e-5, abs=1e-5)
        assert state.cost_basis_usd == pytest.approx(expected_cost[ticker], rel=1e-4, abs=1e-2)


# ── Property 3: Split + dividend reinvestment qty conservation ──────────────


@given(
    pre_qty=st.floats(min_value=1.0, max_value=1000.0, allow_nan=False),
    buy_price=st.floats(min_value=5.0, max_value=500.0, allow_nan=False),
    reinvest_shares=st.floats(min_value=0.001, max_value=10.0, allow_nan=False),
    reinvest_amount=st.floats(min_value=1.0, max_value=1000.0, allow_nan=False),
)
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_replay_split_plus_reinvestment_conserves_state(
    tmp_path: Path,
    pre_qty: float,
    buy_price: float,
    reinvest_shares: float,
    reinvest_amount: float,
) -> None:
    """A 2-for-1 split (modeled as a ``DISTRIBUTION`` row with ``qty=pre_qty``)
    followed by a dividend ``REINVESTMENT`` must yield:

      - ``quantity == pre_qty * 2 + reinvest_shares`` (split doubles, DRIP adds)
      - ``cost_basis_usd == (pre_qty * buy_price) + reinvest_amount``
        (split leaves total cost unchanged; reinvestment adds to cost)

    This is the trickiest replay path because DISTRIBUTION and REINVESTMENT
    sit on different branches (``_POSITION_ONLY_KINDS`` vs BUY/REINVESTMENT
    cost-basis update) — a regression that reclassifies DISTRIBUTION as
    DIVIDEND would drop the split qty silently.
    """
    db = _make_db(tmp_path)
    ticker = "FOO"
    initial_cost = pre_qty * buy_price

    rows: list[tuple[str, str, str, float, float]] = [
        # Day 1: initial BUY.
        ("2024-01-02", ActionKind.BUY.value, ticker, pre_qty, -initial_cost),
        # Day 2: 2-for-1 split adds pre_qty new shares via DISTRIBUTION.
        ("2024-06-15", ActionKind.DISTRIBUTION.value, ticker, pre_qty, 0.0),
        # Day 3: dividend REINVESTMENT adds `reinvest_shares` at `reinvest_amount`.
        ("2024-07-01", ActionKind.REINVESTMENT.value, ticker, reinvest_shares, -reinvest_amount),
    ]
    insert_prop_rows(db, rows)
    result = replay_transactions(db, _PROPERTY_REPLAY, date(2024, 12, 31))

    state = result.positions[("", ticker)]
    expected_qty = pre_qty * 2 + reinvest_shares
    expected_cost = initial_cost + reinvest_amount

    assert state.quantity == pytest.approx(expected_qty, rel=1e-5, abs=1e-5)
    assert state.cost_basis_usd == pytest.approx(expected_cost, rel=1e-4, abs=1e-2)
