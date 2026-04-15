"""Price-only projection of computed_daily forward by a handful of days.

Used by the nightly CI job to advance the networth chart past the last
``computed_daily`` row written by the local pipeline. The projection
assumes **no new transactions** in the window: only prices move. For
tickers priced directly (``daily_close``), reprice via shares × new
price. For synthetic tickers (``401k *``) use their proxy's price ratio.
For ``CNY Assets`` use the inverse USD/CNY rate ratio (same CNY balance,
new USD value). Everything else carries forward unchanged.

Intentionally a narrower code path than ``compute_daily_allocation``:
trades absolute accuracy for zero dependency on Empower/Qianji/Fidelity
account state. When the local pipeline next runs it writes authoritative
rows that replace these projections (Phase 3 flips the D1 sync for
``computed_daily*`` from INSERT OR IGNORE to range-replace).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TypedDict


class TickerSnapshot(TypedDict):
    """Per-ticker output shape — same keys as _build_allocation_row produces."""

    ticker: str
    value: float
    category: str
    subtype: str
    cost_basis: float
    gain_loss: float
    gain_loss_pct: float

# Synthetic 401k tickers → daily_close proxy. Mirrors
# ``etl.sources.empower.PROXY_TICKERS`` but kept local so this module has no
# cross-dependency on the Empower source.
_PROXY_TICKERS: dict[str, str] = {
    "401k sp500": "VOO",
    "401k tech": "QQQM",
    "401k ex-us": "VXUS",
}

_CNY_RATE_SYMBOL = "CNY=X"


@dataclass
class TickerRow:
    """Minimal per-ticker snapshot used as projection input/output."""

    ticker: str
    value: float
    category: str
    subtype: str
    cost_basis: float


@dataclass
class ProjectedDay:
    """Shape matches ``compute_daily_allocation``'s per-day row."""

    date: date
    total: float
    us_equity: float
    non_us_equity: float
    crypto: float
    safe_net: float
    liabilities: float
    tickers: list[TickerSnapshot]


# ── Core per-day math ───────────────────────────────────────────────────────


def _price_ratio(
    ticker: str,
    prices_today: dict[str, float],
    prices_prev: dict[str, float],
) -> float | None:
    """Return ``today / prev`` reprice factor for a ticker, or None.

    None signals "carry forward unchanged" — the caller should not move
    this ticker's value.
    """
    if ticker in _PROXY_TICKERS:
        proxy = _PROXY_TICKERS[ticker]
        t, p = prices_today.get(proxy), prices_prev.get(proxy)
    elif ticker == "CNY Assets":
        # USD value scales inversely with the USD/CNY rate (same CNY balance).
        # Expressing it as 1/rate keeps the caller's ``value *= ratio`` uniform.
        t_rate = prices_today.get(_CNY_RATE_SYMBOL)
        p_rate = prices_prev.get(_CNY_RATE_SYMBOL)
        if t_rate is None or p_rate is None or t_rate <= 0 or p_rate <= 0:
            return None
        t, p = 1.0 / t_rate, 1.0 / p_rate
    else:
        t, p = prices_today.get(ticker), prices_prev.get(ticker)
    if t is None or p is None or p <= 0:
        return None
    return t / p


def project_one_day(
    prev: list[TickerRow],
    prices_today: dict[str, float],
    prices_prev: dict[str, float],
    today: date,
) -> ProjectedDay:
    """Value the portfolio on ``today`` assuming only prices changed since prev."""
    category_totals: dict[str, float] = {}
    total = 0.0
    liabilities = 0.0
    out_tickers: list[TickerSnapshot] = []

    for r in prev:
        ratio = _price_ratio(r.ticker, prices_today, prices_prev)
        new_val = r.value if ratio is None else r.value * ratio
        new_val = round(new_val, 2)

        if r.cost_basis > 0 and new_val >= 0:
            gl = round(new_val - r.cost_basis, 2)
            gl_pct = round(gl / r.cost_basis * 100, 2)
        else:
            gl = 0.0
            gl_pct = 0.0

        out_tickers.append({
            "ticker": r.ticker,
            "value": new_val,
            "category": r.category,
            "subtype": r.subtype,
            "cost_basis": round(r.cost_basis, 2),
            "gain_loss": gl,
            "gain_loss_pct": gl_pct,
        })
        if new_val < 0:
            liabilities += new_val
        else:
            category_totals[r.category] = category_totals.get(r.category, 0) + new_val
            total += new_val

    return ProjectedDay(
        date=today,
        total=round(total, 2),
        us_equity=round(category_totals.get("US Equity", 0), 2),
        non_us_equity=round(category_totals.get("Non-US Equity", 0), 2),
        crypto=round(category_totals.get("Crypto", 0), 2),
        safe_net=round(category_totals.get("Safe Net", 0), 2),
        liabilities=round(liabilities, 2),
        tickers=out_tickers,
    )


# ── Multi-day driver ────────────────────────────────────────────────────────


def _weekdays_strict(start: date, end: date) -> list[date]:
    """Mon-Fri only, matching compute_daily_allocation's skip."""
    out: list[date] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def project_range(
    initial_tickers: list[TickerRow],
    initial_date: date,
    end: date,
    prices_by_date: dict[date, dict[str, float]],
) -> list[ProjectedDay]:
    """Project forward from ``initial_date`` (exclusive) through ``end`` inclusive.

    ``prices_by_date`` must already be forward-filled — callers load from
    ``daily_close`` and ffill before passing in. Each entry is the set of
    per-ticker closes as of that date.
    """
    results: list[ProjectedDay] = []
    state = list(initial_tickers)
    prev_date = initial_date
    prev_prices = prices_by_date.get(prev_date, {})

    for current in _weekdays_strict(initial_date + timedelta(days=1), end):
        today_prices = prices_by_date.get(current, prev_prices)
        projected = project_one_day(state, today_prices, prev_prices, current)
        results.append(projected)
        state = [
            TickerRow(
                ticker=t["ticker"], value=t["value"],
                category=t["category"], subtype=t["subtype"],
                cost_basis=t["cost_basis"],
            )
            for t in projected.tickers
        ]
        prev_date = current
        prev_prices = today_prices

    return results
