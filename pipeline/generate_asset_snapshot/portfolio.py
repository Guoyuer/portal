"""Portfolio loading from CSV file or string content."""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path

from .types import Config, Portfolio, PortfolioError, parse_currency

log = logging.getLogger(__name__)


def _parse_rows(
    reader: csv.DictReader[str], config: Config
) -> tuple[dict[str, float], dict[str, int], dict[str, float], dict[str, float], dict[str, float]]:
    """Parse CSV rows into totals, counts, cost_basis, gain_loss, gain_loss_pct."""
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    cost_basis: dict[str, float] = defaultdict(float)
    gain_loss: dict[str, float] = defaultdict(float)
    gain_loss_pct: dict[str, float] = {}

    headers = {h.lower(): h for h in (reader.fieldnames or [])}
    sym_h = headers.get("symbol")
    desc_h = headers.get("description")
    val_h = headers.get("current value")
    cb_h = headers.get("cost basis total")
    gl_h = headers.get("total gain/loss dollar")
    if not all([sym_h, desc_h, val_h]):
        raise PortfolioError("Missing required CSV headers: Symbol, Description, Current Value")

    for row in reader:
        symbol = (row.get(sym_h) or row.get(desc_h) or "").strip().rstrip("*")
        if not symbol or symbol.lower() == "pending activity":
            continue
        ticker = config["aliases"].get(symbol, symbol)
        if ticker not in config["assets"]:
            raise PortfolioError(
                f"Error: Ticker '{ticker}' (from symbol '{symbol}') "
                f"is not configured in config.json. "
                f"Please add it to the 'assets' section."
            )
        totals[ticker] += parse_currency(row.get(val_h, "0"))
        counts[ticker] += 1
        if cb_h:
            cost_basis[ticker] += parse_currency(row.get(cb_h, "0"))
        if gl_h:
            gain_loss[ticker] += parse_currency(row.get(gl_h, "0"))

    # Compute gain/loss % from aggregated values
    for ticker in totals:
        cb = cost_basis[ticker]
        if cb > 0:
            gain_loss_pct[ticker] = (totals[ticker] - cb) / cb * 100
        else:
            gain_loss_pct[ticker] = 0.0

    return totals, counts, cost_basis, gain_loss, gain_loss_pct


def load_portfolio(
    csv_path: Path,
    config: Config,
    manual_values: dict[str, float] | None = None,
) -> Portfolio:
    """Load portfolio from a Fidelity CSV file with optional manual assets."""
    try:
        with csv_path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            totals, counts, cost_basis, gain_loss, gain_loss_pct = _parse_rows(reader, config)
    except FileNotFoundError as e:
        raise PortfolioError(f"CSV not found: {csv_path}") from e
    for ticker, value in (manual_values or {}).items():
        totals[ticker] += value
        counts[ticker] += 1
    p = Portfolio(
        totals=totals,
        counts=counts,
        total=sum(totals.values()),
        cost_basis=cost_basis,
        gain_loss=gain_loss,
        gain_loss_pct=gain_loss_pct,
    )
    log.info("Portfolio: %d tickers, %d lots, total $%s (manual: %d)", len(p["totals"]), sum(p["counts"].values()), f"{p['total']:,.2f}", len(manual_values or {}))
    return p
