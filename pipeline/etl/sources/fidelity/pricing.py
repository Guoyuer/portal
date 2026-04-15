"""Ticker-level position valuation for Fidelity holdings.

Owns:
  - CUSIP detection (8+ digit, leading-digit symbols → ``T-Bills`` bucket at
    face quantity).
  - Mutual-fund T-1 price dating — yfinance stamps MF NAV with the wrong
    date, so mutual funds look up ``mf_price_date`` instead of ``price_date``.
  - Regular-symbol price lookup via :class:`PriceContext.lookup`. Tickers
    with no price on the resolved date are logged and excluded (mirrors the
    pre-refactor ``_add_fidelity_positions`` warning).

Output is a flat :class:`list[PositionRow]` that the allocation engine
aggregates alongside the cash + 401k rows.
"""
from __future__ import annotations

import logging

from etl.parsing import is_cusip
from etl.sources import PositionRow, PriceContext

log = logging.getLogger(__name__)

# Default set of mutual-fund tickers that need T-1 price lookup. Mirrors the
# legacy ``allocation._MUTUAL_FUNDS`` constant so that behaviour stays
# unchanged when ``config`` is missing the ``mutual_funds`` key.
_DEFAULT_MUTUAL_FUNDS: frozenset[str] = frozenset({"FXAIX", "FSSNX", "FNJHX"})


def mutual_funds(config: dict[str, object]) -> frozenset[str]:
    """Return the user-configured mutual-fund ticker set, or the default."""
    raw = config.get("mutual_funds")
    if isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset(str(t) for t in raw)
    return _DEFAULT_MUTUAL_FUNDS


def position_rows(
    positions: dict[tuple[str, str], float],
    cost_basis: dict[tuple[str, str], float],
    prices: PriceContext,
    mutual_fund_set: frozenset[str],
) -> list[PositionRow]:
    """Turn ``{(account, symbol): qty}`` + cost basis into per-holding rows.

    Applies the CUSIP → ``T-Bills`` rule, then walks each remaining
    ``(account, symbol)`` pair through :meth:`PriceContext.lookup`. Symbols
    in ``mutual_fund_set`` use the T-1 ``mf_price_date`` branch. Symbols
    with no price on the resolved date log a warning and are dropped.
    """
    rows: list[PositionRow] = []

    for (acct, sym), qty in positions.items():
        cb = cost_basis.get((acct, sym))

        if is_cusip(sym):
            # T-Bill CUSIP: face value quantity, bucketed under "T-Bills".
            rows.append(PositionRow(
                ticker="T-Bills",
                value_usd=qty,
                quantity=qty,
                cost_basis_usd=cb,
                account=acct,
            ))
            continue

        price = prices.lookup(sym, mutual_fund=sym in mutual_fund_set)
        if price is not None:
            rows.append(PositionRow(
                ticker=sym,
                value_usd=qty * price,
                quantity=qty,
                cost_basis_usd=cb,
                account=acct,
            ))
            continue
        p_date = prices.mf_price_date if sym in mutual_fund_set else prices.price_date
        log.warning(
            "No price for %s on %s (holding %.3f shares) — excluded from allocation",
            sym, p_date, qty,
        )

    return rows
