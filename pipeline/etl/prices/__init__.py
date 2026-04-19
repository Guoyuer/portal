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
``_build_split_factors`` / ``_reverse_split_factor`` /
``_holding_periods_from_action_kind_rows`` are re-exported too because
``sync_prices_nightly.py`` (the D1 companion script) reaches in for them.
"""
from __future__ import annotations

from datetime import date, timedelta

# Defined before the submodule imports so ``.fetch`` can pick it up via
# ``from . import refresh_window_start`` during partial package init.
REFRESH_WINDOW_DAYS = 7


def refresh_window_start(end: date) -> date:
    """Earliest date in the refresh window — inclusive
    ``[refresh_window_start(end), end]`` spans exactly ``REFRESH_WINDOW_DAYS``
    calendar days. Rows strictly before this boundary are immutable once
    persisted; rows inside may be overwritten on the next build to pick up
    late yfinance corrections (see PR #156)."""
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
