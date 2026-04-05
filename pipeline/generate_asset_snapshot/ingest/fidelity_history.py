"""Parse Fidelity Accounts History CSV into structured transaction records.

The real CSV exported from Fidelity has 2 blank lines before the header row.
Action strings are verbose and must be classified into canonical action_types.
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action classification
# ---------------------------------------------------------------------------
from ..types import (
    ACT_BUY,
    ACT_COLLATERAL,
    ACT_DEPOSIT,
    ACT_DIVIDEND,
    ACT_FOREIGN_TAX,
    ACT_INTEREST,
    ACT_IRA_CONTRIBUTION,
    ACT_LENDING,
    ACT_OTHER,
    ACT_REINVESTMENT,
    ACT_ROTH_CONVERSION,
    ACT_SELL,
    ACT_TRANSFER,
    FidelityTransaction,
)

_ACTION_RULES: list[tuple[str, str]] = [
    # Order matters: more specific patterns first
    ("FOREIGN TAX", ACT_FOREIGN_TAX),
    ("REINVESTMENT", ACT_REINVESTMENT),
    ("DIVIDEND RECEIVED", ACT_DIVIDEND),
    ("YOU BOUGHT", ACT_BUY),
    ("YOU SOLD", ACT_SELL),
    ("Electronic Funds Transfer", ACT_DEPOSIT),
    ("CASH CONTRIBUTION", ACT_IRA_CONTRIBUTION),
    ("CONV TO ROTH", ACT_ROTH_CONVERSION),
    ("ROTH CONVERSION", ACT_ROTH_CONVERSION),
    ("TRANSFERRED", ACT_TRANSFER),
    ("INTEREST", ACT_INTEREST),
    ("YOU LOANED", ACT_LENDING),
    ("LOAN RETURNED", ACT_LENDING),
    ("INCREASE COLLATERAL", ACT_COLLATERAL),
    ("DECREASE COLLATERAL", ACT_COLLATERAL),
]


def _classify_action(raw_action: str) -> str:
    """Map a verbose Fidelity action string to a canonical action_type."""
    upper = raw_action.upper()
    for pattern, action_type in _ACTION_RULES:
        if pattern.upper() in upper:
            return action_type
    return ACT_OTHER


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------


def _parse_float(value: str) -> float:
    """Parse a numeric string, returning 0.0 for empty/missing values."""
    if not value or not value.strip():
        return 0.0
    cleaned = value.strip().replace(",", "").replace("$", "")
    if not cleaned:
        return 0.0
    return float(cleaned)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _parse_csv_text(text: str) -> list[FidelityTransaction]:
    """Parse Fidelity history CSV text into structured transaction records."""
    # Strip BOM if present
    if text.startswith("\ufeff"):
        text = text[1:]

    lines = text.splitlines(keepends=True)

    # Skip leading blank lines to find the header
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1

    csv_text = "".join(lines[start:])
    reader = csv.DictReader(csv_text.splitlines())

    transactions: list[FidelityTransaction] = []
    for row in reader:
        run_date = (row.get("Run Date") or "").strip()
        if not run_date:
            continue
        # Skip footer/disclaimer rows — real dates match MM/DD/YYYY
        if not re.match(r"\d{2}/\d{2}/\d{4}", run_date):
            continue

        raw_action = (row.get("Action") or "").strip()
        symbol = (row.get("Symbol") or "").strip()
        description = (row.get("Description") or "").strip()
        account = (row.get("Account Number") or "").strip()
        quantity = _parse_float(row.get("Quantity", ""))
        price = _parse_float(row.get("Price", ""))
        amount = _parse_float(row.get("Amount", ""))
        action_type = _classify_action(raw_action)

        txn: FidelityTransaction = {
            "date": run_date,
            "account": account,
            "action_type": action_type,
            "symbol": symbol,
            "description": description,
            "quantity": quantity,
            "price": price,
            "amount": amount,
            "raw_action": raw_action,
            "dedup_key": (run_date, account, raw_action, symbol, amount),
        }
        transactions.append(txn)

    return transactions


def load_transactions(csv_path: Path) -> list[FidelityTransaction]:
    """Load Fidelity Accounts History CSV from a file path."""
    txns = _parse_csv_text(csv_path.read_text(encoding="utf-8-sig"))
    by_type: dict[str, int] = {}
    for t in txns:
        by_type[t["action_type"]] = by_type.get(t["action_type"], 0) + 1
    log.info("Transactions: %d from %s (%s)", len(txns), csv_path.name, ", ".join(f"{t}={c}" for t, c in sorted(by_type.items())))
    return txns
