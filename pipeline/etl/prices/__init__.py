"""Historical price fetching and caching via timemachine.db.

Split into three cohesive submodules:
  * :mod:`store` — DB read/write helpers (``load_prices``,
    ``load_cny_rates``, ``symbol_holding_periods_from_db``, the private
    ``_persist_close*`` / ``_cached_range`` / holding-period primitives).
  * :mod:`fetch` — Yahoo I/O (``fetch_and_store_prices``,
    ``fetch_and_store_cny_rates``, split factor fetching and pre-split
    reversal). Depends on ``store`` and ``validate``.
  * :mod:`validate` — split cross-validation (``SplitValidationError``,
    ``_validate_splits_against_transactions``). Raises before prices are
    persisted when Yahoo and Fidelity disagree.

Public API is re-exported from this package; external callers continue to
``from etl.prices import ...`` without caring about the submodule layout.
The ``sync_prices_nightly.py`` D1 companion script imports the
``_build_split_factors`` / ``_reverse_split_factor`` /
``_holding_periods_from_action_kind_rows`` internals — these are
intentionally re-exported alongside the public surface.

The refresh-window boundary (``REFRESH_WINDOW_DAYS`` + ``refresh_window_start``)
lives here too: a ``daily_close`` or ``computed_daily`` row is considered
*mutable* (may be overwritten on the next pipeline run to pick up late
yfinance corrections or recomputed values from updated prices) for exactly
``REFRESH_WINDOW_DAYS`` calendar days ending at the run's ``end`` date. Dates
strictly before the boundary are treated as immutable historical fact once
persisted. Keeping the math here avoided the drift PR #156 surfaced —
prices.py used an 8-day window while build / sync_prices_nightly used 7,
producing subtle boundary discrepancies on the mutable/immutable split.
"""
from __future__ import annotations

from datetime import date, timedelta

# Defined *before* the submodule imports below so that ``.fetch`` — which uses
# this primitive — can pick it up from the partially-initialised package
# namespace via ``from . import refresh_window_start``. See the module
# docstring above for why this lives here.
REFRESH_WINDOW_DAYS = 7


def refresh_window_start(end: date) -> date:
    """Earliest date in the refresh window.

    The refresh window is ``[refresh_window_start(end), end]`` inclusive —
    exactly ``REFRESH_WINDOW_DAYS`` calendar days. Dates strictly before this
    boundary are immutable once persisted.
    """
    return end - timedelta(days=REFRESH_WINDOW_DAYS - 1)


from .fetch import (  # noqa: E402 — after refresh_window_start so fetch can see it
    _build_split_factors,
    _reverse_split_factor,
    fetch_and_store_cny_rates,
    fetch_and_store_prices,
)
from .store import (  # noqa: E402
    _holding_periods_from_action_kind_rows,
    load_cny_rates,
    load_prices,
    symbol_holding_periods_from_db,
)
from .validate import (  # noqa: E402
    SPLIT_QTY_TOLERANCE,
    SplitValidationError,
    _validate_splits_against_transactions,
)

__all__ = [
    "REFRESH_WINDOW_DAYS",
    "SPLIT_QTY_TOLERANCE",
    "SplitValidationError",
    "_build_split_factors",
    "_holding_periods_from_action_kind_rows",
    "_reverse_split_factor",
    "_validate_splits_against_transactions",
    "fetch_and_store_cny_rates",
    "fetch_and_store_prices",
    "load_cny_rates",
    "load_prices",
    "refresh_window_start",
    "symbol_holding_periods_from_db",
]
