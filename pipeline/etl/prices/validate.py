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
    """Two-way cross-check Yahoo splits vs Fidelity ``DISTRIBUTION`` rows.

    Called by :func:`etl.prices.fetch.fetch_and_store_prices` immediately after
    :func:`etl.prices.fetch._build_split_factors` returns, *before* any
    ``daily_close`` rows are persisted. See :class:`SplitValidationError` for
    the two invariants. ``today`` is injected by tests; production callers leave
    it ``None`` and the function uses :meth:`date.today`.

    Inputs
    ------
    - ``conn`` — open :class:`sqlite3.Connection` to ``timemachine.db`` (reads
      ``fidelity_transactions`` only; never writes).
    - ``holding_periods`` — ``{symbol: (first_buy_date, last_sell_date_or_None)}``
      produced by :func:`etl.prices.store._holding_periods_from_action_kind_rows`.
      ``None`` on the right means "still held as of today".
    - ``split_factors`` — ``{symbol: [(split_date, ratio), ...]}`` from
      :func:`etl.prices.fetch._build_split_factors` (Yahoo's ``.splits`` feed).
    - ``today`` — override for testability; production path uses :meth:`date.today`.

    Algorithm (step by step)
    ------------------------
    1. Iterate ``split_factors`` symbol-by-symbol. Symbols that appear in
       Yahoo but are absent from ``holding_periods`` (i.e. we never held them)
       are skipped silently — we don't care about splits outside holdings.
    2. For each ``(split_date, ratio)`` pair, resolve the holding window:
       ``hp_start = first_buy_date`` / ``hp_end = last_sell_date_or today``.
       A half-open check ``hp_start < split_date <= hp_end`` gates entry —
       splits on the buy date itself are ignored (the buy already reflects
       the post-split share count), and splits after the final sell are
       irrelevant for pre-split price reversal.
    3. Sum ``quantity`` from all ``fidelity_transactions`` rows for that
       symbol with ``run_date < split_date`` across the material action kinds
       (``buy``, ``sell``, ``reinvestment``, ``distribution``, ``redemption``,
       ``exchange``, ``transfer``) — this is ``pre_qty``, the share count
       going *into* the split. If ``pre_qty < SPLIT_QTY_TOLERANCE`` (user
       held effectively zero shares that day), the split is skipped.
    4. Sum ``quantity`` of ``DISTRIBUTION`` rows dated exactly on
       ``split_date`` — this is ``actual``, the share delta Fidelity
       recorded. Forward splits come through as ``DISTRIBUTION`` with
       ``quantity > 0`` equal to ``pre_qty × (ratio - 1)`` (e.g. 2:1 → +1
       share per held, 3:1 → +2). Dividend distributions are a different
       ``action_kind``, so this query isolates the split leg.
    5. Compare ``actual`` with ``expected = pre_qty * (ratio - 1)``. If
       ``|expected - actual| > SPLIT_QTY_TOLERANCE`` (0.01 absolute, sub-share
       tolerance — Fidelity always rounds to whole shares so anything above
       this is real drift), append a mismatch string and continue.
    6. Record ``(symbol, split_date)`` in ``checked_pairs`` so direction 2
       doesn't double-fire on the same day.
    7. **Direction 2** (reverse check): iterate ``fidelity_transactions`` for
       every ``(symbol, run_date, SUM(quantity))`` with ``action_kind = 'distribution'``
       and ``quantity > 0``. For each, skip pairs already covered by direction 1.
       Any remaining pair means Fidelity recorded a split-like quantity
       delta on a date with no matching Yahoo entry — almost always a silent
       :func:`etl.prices.fetch._build_split_factors` failure. Without a Yahoo
       entry, :func:`etl.prices.fetch._reverse_split_factor` would return 1.0
       for pre-split dates and leave Yahoo's retroactively-adjusted prices
       un-reversed; that is a corruption scenario, so the row is added as a
       mismatch.
    8. If any mismatches accumulated, build a multi-line message (one line
       per issue) and raise :class:`SplitValidationError`. Caller (the ETL
       step) is responsible for not persisting prices on this path.

    Return / raise contract
    -----------------------
    Returns ``None`` on success (silent). Raises :class:`SplitValidationError`
    with every detected mismatch aggregated into one message — the ETL flow
    is designed so the operator sees all drift in a single failure run
    rather than whack-a-mole fail-on-first-error.

    Known limitations
    -----------------
    - **No fuzzy date window.** Yahoo and Fidelity are expected to report
      splits on the same calendar date. If Fidelity posts the DISTRIBUTION
      row a day late (rare but possible around month-end), direction 1 will
      report "expected > 0, got 0" and direction 2 will report the orphan
      Fidelity row. Both surface the same truth, but the message is
      duplicated — review both lines before concluding there are two bugs.
    - **Reverse splits are not modeled.** The ``pre_qty × (ratio - 1)``
      formula produces a negative expected delta for ``ratio < 1`` (e.g. a
      1:10 reverse split → ratio 0.1 → expected = -0.9 × pre_qty). Fidelity
      records reverse splits as a pair of ``REDEMPTION`` + ``DISTRIBUTION``
      rows; only the ``DISTRIBUTION`` leg is queried here. If a reverse
      split is ever held, this function will false-positive.
    - **Direction 2 aggregates by ``(symbol, run_date)`` only.** Multiple
      distinct ``DISTRIBUTION`` events on the same day (possible for some
      ETFs that pay both a split and a special dividend) collapse into a
      single row. Direction 1 handles multi-event days correctly via the
      single ``expected`` compare, but the reverse check can't disambiguate.
    - **Sub-share tolerance assumes Fidelity rounds to whole shares.** This
      holds for every symbol encountered to date; a future broker source
      with fractional-share split deltas would need a tighter
      :data:`SPLIT_QTY_TOLERANCE` or a ratio-aware check.
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
