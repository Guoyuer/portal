"""Portfolio loading from CSV file or string content."""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .types import Config, Portfolio, PortfolioError, parse_currency

log = logging.getLogger(__name__)


@dataclass
class ParsedRows:
    """Aggregated per-ticker values parsed from a Fidelity positions CSV."""
    totals: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    cost_basis: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    gain_loss: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    gain_loss_pct: dict[str, float] = field(default_factory=dict)


def _parse_rows(reader: csv.DictReader[str], config: Config) -> ParsedRows:
    """Parse CSV rows into per-ticker totals, counts, cost basis, and gain/loss."""
    parsed = ParsedRows()

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
        parsed.totals[ticker] += parse_currency(row.get(val_h, "0"))
        parsed.counts[ticker] += 1
        if cb_h:
            parsed.cost_basis[ticker] += parse_currency(row.get(cb_h, "0"))
        if gl_h:
            parsed.gain_loss[ticker] += parse_currency(row.get(gl_h, "0"))

    # Compute gain/loss % from aggregated values
    for ticker in parsed.totals:
        cb = parsed.cost_basis[ticker]
        parsed.gain_loss_pct[ticker] = (parsed.totals[ticker] - cb) / cb * 100 if cb > 0 else 0.0

    return parsed


def load_portfolio(
    csv_path: Path,
    config: Config,
    manual_values: dict[str, float] | None = None,
) -> Portfolio:
    """Load portfolio from a Fidelity CSV file with optional manual assets."""
    try:
        with csv_path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            parsed = _parse_rows(reader, config)
    except FileNotFoundError as e:
        raise PortfolioError(f"CSV not found: {csv_path}") from e
    for ticker, value in (manual_values or {}).items():
        parsed.totals[ticker] += value
        parsed.counts[ticker] += 1
    p = Portfolio(
        totals=parsed.totals,
        counts=parsed.counts,
        total=sum(parsed.totals.values()),
        cost_basis=parsed.cost_basis,
        gain_loss=parsed.gain_loss,
        gain_loss_pct=parsed.gain_loss_pct,
    )
    log.info("Portfolio: %d tickers, %d lots, total $%s (manual: %d)", len(p["totals"]), sum(p["counts"].values()), f"{p['total']:,.2f}", len(manual_values or {}))
    return p
