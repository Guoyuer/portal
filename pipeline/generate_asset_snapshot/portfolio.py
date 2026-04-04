"""Portfolio loading from CSV file or string content."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from .types import CURRENCY_RE, Config, Portfolio, PortfolioError


def _parse_rows(reader: csv.DictReader[str], config: Config) -> tuple[dict[str, float], dict[str, int]]:
    """Parse CSV rows into totals and counts dicts. Shared by file and string loaders."""
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)

    headers = {h.lower(): h for h in (reader.fieldnames or [])}
    sym_h = headers.get("symbol")
    desc_h = headers.get("description")
    val_h = headers.get("current value")
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
        val = row.get(val_h, "0").strip()
        amount = float(CURRENCY_RE.sub("", val)) if val and val != "--" else 0.0
        totals[ticker] += amount
        counts[ticker] += 1

    return totals, counts


def _apply_manual(totals: dict[str, float], counts: dict[str, int], config: Config) -> Portfolio:
    """Add manual assets and return final Portfolio."""
    for ticker, value in config["manual"].items():
        totals[ticker] += value
        counts[ticker] += 1
    return Portfolio(totals=totals, counts=counts, total=sum(totals.values()))


def load_portfolio(csv_path: Path, config: Config) -> Portfolio:
    """Load portfolio from a Fidelity CSV file."""
    try:
        with csv_path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            totals, counts = _parse_rows(reader, config)
    except FileNotFoundError as e:
        raise PortfolioError(f"CSV not found: {csv_path}") from e
    return _apply_manual(totals, counts, config)
