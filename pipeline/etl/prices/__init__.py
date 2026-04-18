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
"""
from __future__ import annotations

from .fetch import (
    _build_split_factors,
    _reverse_split_factor,
    fetch_and_store_cny_rates,
    fetch_and_store_prices,
)
from .store import (
    _holding_periods_from_action_kind_rows,
    load_cny_rates,
    load_prices,
    symbol_holding_periods_from_db,
)
from .validate import (
    SPLIT_QTY_TOLERANCE,
    SplitValidationError,
    _validate_splits_against_transactions,
)

__all__ = [
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
    "symbol_holding_periods_from_db",
]
