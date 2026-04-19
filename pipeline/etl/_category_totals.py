"""Shared per-day category-rollup fold for allocation + projection.

Both :func:`etl.allocation._build_allocation_row` and
:func:`etl.projection.project_one_day` reduce a stream of per-ticker
``(value, category)`` pairs into the same 6-field rollup: a grand total,
a negatives-only liabilities bucket, and one positive-only bucket per
canonical category (``US Equity`` / ``Non-US Equity`` / ``Crypto`` /
``Safe Net``). Kept in a tiny standalone module so :mod:`etl.projection`
can consume it without pulling in :mod:`etl.allocation`'s full
pandas/Qianji dependency graph.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class CategoryTotals:
    """Rounded per-category rollup ready for :class:`AllocationRow` /
    :class:`ProjectedDay` construction.

    Mirrors the four Okabe-Ito canonical buckets the frontend renders.
    Non-canonical categories (e.g. ``Liability`` that
    :func:`etl.allocation._categorize_ticker` tags negative-value tickers
    with) are absorbed into :attr:`liabilities` via the negative-value
    branch — :attr:`category_totals` carries only the positive-value
    rollup, so a non-canonical category name on a positive value would
    silently drop out of the 4 fields below while still contributing to
    :attr:`total`.
    """

    total: float
    liabilities: float
    us_equity: float
    non_us_equity: float
    crypto: float
    safe_net: float


def accumulate_category_totals(pairs: Iterable[tuple[float, str]]) -> CategoryTotals:
    """Fold ``(value, category)`` pairs into a :class:`CategoryTotals`.

    Negative values flow into ``liabilities`` regardless of category (the
    portfolio-liability bucket). Non-negative values roll up by category
    and into ``total``. Zero values contribute to neither — but are not
    short-circuited; callers that want to drop zeros should pre-filter
    upstream (``allocation`` does, ``projection`` does not, matching the
    pre-refactor behaviour exactly).
    """
    category_totals: dict[str, float] = {}
    total = 0.0
    liabilities = 0.0
    for value, category in pairs:
        if value < 0:
            liabilities += value
        else:
            category_totals[category] = category_totals.get(category, 0.0) + value
            total += value
    return CategoryTotals(
        total=round(total, 2),
        liabilities=round(liabilities, 2),
        us_equity=round(category_totals.get("US Equity", 0.0), 2),
        non_us_equity=round(category_totals.get("Non-US Equity", 0.0), 2),
        crypto=round(category_totals.get("Crypto", 0.0), 2),
        safe_net=round(category_totals.get("Safe Net", 0.0), 2),
    )
