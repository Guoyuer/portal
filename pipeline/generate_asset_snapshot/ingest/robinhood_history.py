"""Parse Robinhood activity report CSV.

Robinhood CSV columns:
  Activity Date, Process Date, Settle Date, Instrument, Description,
  Trans Code, Quantity, Price, Amount

Trans codes:
  Buy/Sell — stock trades (Quantity, Price, Amount present)
  CDIV     — cash dividend (Amount only)
  ACH      — cash deposit/withdrawal (Amount only)
  SLIP     — stock spin-off/split
  AFEE/DFEE — ADR fees
  RTP      — return of principal
  REC      — received (stock transfer in)
"""
from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path
from typing import Any

from ..timemachine import _parse_date
from ..types import parse_float as _parse_float


def _parse_amount(s: str) -> float:
    """Parse Robinhood amount: '$1,234.56' or '($1,234.56)' (negative)."""
    if not s or not s.strip():
        return 0.0
    cleaned = s.strip().replace("$", "").replace(",", "")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        return -float(cleaned[1:-1])
    return float(cleaned)


def load_robinhood_csv(csv_path: Path) -> list[dict[str, Any]]:
    """Load and parse a Robinhood activity report CSV.

    Returns list of normalized dicts with keys:
      date, instrument, trans_code, quantity, price, amount, description
    """
    text = csv_path.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    rows: list[dict[str, Any]] = []

    for record in reader:
        activity_date = (record.get("Activity Date") or "").strip()
        if not activity_date or not re.match(r"\d{1,2}/\d{1,2}/\d{4}", activity_date):
            continue

        rows.append({
            "date": _parse_date(activity_date),
            "instrument": (record.get("Instrument") or "").strip(),
            "trans_code": (record.get("Trans Code") or "").strip(),
            "quantity": _parse_float(record.get("Quantity", "")),
            "price": _parse_float(record.get("Price", "")),
            "amount": _parse_amount(record.get("Amount", "")),
            "description": (record.get("Description") or "").strip(),
        })

    return rows


def replay_robinhood(csv_path: Path, as_of: date | None = None) -> dict[str, Any]:
    """Replay Robinhood transactions up to as_of, return positions and cost basis.

    Returns:
        {
            "positions": {symbol: quantity, ...},
            "cost_basis": {symbol: total_cost, ...},
            "cash": float,  (net ACH deposits - withdrawals)
            "dividends": float,
        }
    """
    rows = load_robinhood_csv(csv_path)

    positions: dict[str, float] = {}
    cost_basis: dict[str, float] = {}
    cash = 0.0
    dividends = 0.0

    for row in rows:
        if as_of and row["date"] > as_of:
            continue

        code = row["trans_code"]
        sym = row["instrument"]
        qty = row["quantity"]
        amt = row["amount"]

        if code == "Buy" and sym:
            positions[sym] = positions.get(sym, 0) + qty
            cost_basis[sym] = cost_basis.get(sym, 0) + abs(amt)
        elif code == "Sell" and sym:
            # Reduce cost basis proportionally (average cost)
            if positions.get(sym, 0) > 0:
                sold_fraction = min(abs(qty) / positions[sym], 1.0)
                cost_basis[sym] = cost_basis.get(sym, 0) * (1 - sold_fraction)
            positions[sym] = positions.get(sym, 0) - abs(qty)
        elif code == "CDIV":
            dividends += amt
        elif code == "ACH":
            cash += amt
        elif code in ("SLIP", "REC") and sym:
            # Stock received (spin-off, transfer)
            positions[sym] = positions.get(sym, 0) + qty

    # Clean up zero/tiny positions
    positions = {k: round(v, 6) for k, v in positions.items() if abs(v) > 0.001}
    cost_basis = {k: round(v, 2) for k, v in cost_basis.items() if k in positions}

    return {
        "positions": positions,
        "cost_basis": cost_basis,
        "cash": round(cash, 2),
        "dividends": round(dividends, 2),
    }
