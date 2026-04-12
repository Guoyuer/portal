"""Parse Fidelity Accounts History CSV into structured transaction records.

The real CSV exported from Fidelity has 2 blank lines before the header row.
Action strings are verbose and must be classified into canonical action_types.

Dates are normalized to ISO ``YYYY-MM-DD`` at ingestion — every downstream
consumer (DB rows, pipeline internals, API responses) sees ISO only. The
Fidelity CSV interchange format (``MM/DD/YYYY``) stays confined to this
module's boundary.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from ..db import get_connection
from ..parsing import STRICT_US_DATE_RE, parse_us_date
from ..types import (
    ACT_BUY,
    ACT_COLLATERAL,
    ACT_DEPOSIT,
    ACT_DISTRIBUTION,
    ACT_DIVIDEND,
    ACT_EXCHANGE,
    ACT_FOREIGN_TAX,
    ACT_INTEREST,
    ACT_IRA_CONTRIBUTION,
    ACT_LENDING,
    ACT_OTHER,
    ACT_REDEMPTION,
    ACT_REINVESTMENT,
    ACT_ROTH_CONVERSION,
    ACT_SELL,
    ACT_TRANSFER,
    ACT_WITHDRAWAL,
    FidelityTransaction,
)
from ..types import (
    parse_float as _parse_float,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action classification
# ---------------------------------------------------------------------------

_ACTION_RULES: list[tuple[str, str]] = [
    # Order matters: more specific patterns first
    ("FOREIGN TAX", ACT_FOREIGN_TAX),
    ("REINVESTMENT", ACT_REINVESTMENT),
    ("DIVIDEND RECEIVED", ACT_DIVIDEND),
    ("REDEMPTION PAYOUT", ACT_REDEMPTION),
    ("YOU BOUGHT", ACT_BUY),
    ("YOU SOLD", ACT_SELL),
    ("DISTRIBUTION", ACT_DISTRIBUTION),
    ("EXCHANGED TO", ACT_EXCHANGE),
    ("Electronic Funds Transfer", ACT_DEPOSIT),
    ("DIRECT DEPOSIT", ACT_DEPOSIT),
    ("CASH CONTRIBUTION", ACT_IRA_CONTRIBUTION),
    ("CONV TO ROTH", ACT_ROTH_CONVERSION),
    ("ROTH CONVERSION", ACT_ROTH_CONVERSION),
    ("EARLY DIST", ACT_TRANSFER),
    ("PARTIAL CY RECHAR", ACT_TRANSFER),
    ("ROLLOVER CASH", ACT_TRANSFER),
    ("TRANSFERRED", ACT_TRANSFER),
    ("INTEREST", ACT_INTEREST),
    ("YOU LOANED", ACT_LENDING),
    ("LOAN RETURNED", ACT_LENDING),
    ("INCREASE COLLATERAL", ACT_COLLATERAL),
    ("DECREASE COLLATERAL", ACT_COLLATERAL),
    ("CASH ADVANCE", ACT_WITHDRAWAL),
    ("DIRECT DEBIT", ACT_WITHDRAWAL),
    ("DEBIT CARD", ACT_WITHDRAWAL),
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _parse_csv_text(text: str, *, source: str = "CSV text") -> list[FidelityTransaction]:
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
    for row_num, row in enumerate(reader, start=2):
        run_date = (row.get("Run Date") or "").strip()
        if not run_date:
            continue
        # Skip footer/disclaimer rows — anything that doesn't look like a
        # date is assumed to be narrative text and is dropped silently.
        if not STRICT_US_DATE_RE.match(run_date):
            continue

        iso_date = parse_us_date(run_date, strict=True, row_context=f"{source} row {row_num}")

        raw_action = (row.get("Action") or "").strip()
        symbol = (row.get("Symbol") or "").strip()
        description = (row.get("Description") or "").strip()
        account = (row.get("Account Number") or "").strip()
        lot_type = (row.get("Type") or "").strip()
        quantity = _parse_float(row.get("Quantity", ""))
        price = _parse_float(row.get("Price", ""))
        amount = _parse_float(row.get("Amount", ""))
        action_type = _classify_action(raw_action)
        # EFT with negative amount is a withdrawal, not a deposit
        if action_type == ACT_DEPOSIT and amount < 0:
            action_type = ACT_WITHDRAWAL

        txn: FidelityTransaction = {
            "date": iso_date,
            "account": account,
            "action_type": action_type,
            "symbol": symbol,
            "description": description,
            "lot_type": lot_type,
            "quantity": quantity,
            "price": price,
            "amount": amount,
            "raw_action": raw_action,
            "dedup_key": (iso_date, account, raw_action, symbol, amount),
        }
        transactions.append(txn)

    return transactions


def load_transactions(csv_path: Path) -> list[FidelityTransaction]:
    """Load Fidelity Accounts History CSV from a file path."""
    txns = _parse_csv_text(csv_path.read_text(encoding="utf-8-sig"), source=csv_path.name)
    by_type: dict[str, int] = {}
    for t in txns:
        by_type[t["action_type"]] = by_type.get(t["action_type"], 0) + 1
    log.info("Transactions: %d from %s (%s)", len(txns), csv_path.name, ", ".join(f"{t}={c}" for t, c in sorted(by_type.items())))
    return txns


# ---------------------------------------------------------------------------
# Ingestion into timemachine database
# ---------------------------------------------------------------------------


def ingest_fidelity_csv(db_path: Path, csv_path: Path) -> int:
    """Ingest a Fidelity CSV into the database, replacing overlapping date ranges.

    Each Fidelity CSV export is an authoritative snapshot of its own date range,
    so a new CSV's contents supersede any existing rows in that range. We
    DELETE rows whose ``run_date`` falls within this CSV's min/max and then
    INSERT every parsed row. No row-level dedup: two legitimate trades with
    identical ``(run_date, action, symbol, quantity, price, amount)`` are
    indistinguishable from CSV alone and preserving both matches reality better
    than collapsing them. Use ``scripts/verify_positions.py`` against a
    ``Portfolio_Positions_*.csv`` snapshot to confirm share-count invariants.

    Dates are normalized from Fidelity's ``MM/DD/YYYY`` to ISO ``YYYY-MM-DD``
    at write time so the database only ever carries ISO dates.

    Returns the total row count in fidelity_transactions after ingestion.
    """
    # Read CSV, handling BOM and leading blank lines
    text = csv_path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()

    # Find the header line (starts with "Run Date")
    header_idx = -1
    for i, line in enumerate(lines):
        if line.strip().startswith("Run Date"):
            header_idx = i
            break
    if header_idx == -1:
        msg = f"No header row found in {csv_path}"
        raise ValueError(msg)

    # Parse with csv.DictReader from the header line onward
    reader = csv.DictReader(lines[header_idx:])
    rows: list[tuple[str, str, str, str, str, str, str, str, float, float, float, str]] = []
    iso_dates: list[str] = []

    # DictReader consumes the header, so the first data row is file line header_idx + 2
    for offset, record in enumerate(reader):
        run_date_raw = record.get("Run Date", "").strip()
        # Skip blank rows and footer/disclaimer text; only rows shaped like a
        # Fidelity date participate in ingestion.
        if not STRICT_US_DATE_RE.match(run_date_raw):
            continue

        iso_date = parse_us_date(
            run_date_raw,
            strict=True,
            row_context=f"{csv_path.name} line {header_idx + 2 + offset}",
        )

        raw_action = record.get("Action", "").strip().strip('"')
        rows.append((
            iso_date,
            record.get("Account", "").strip().strip('"'),
            record.get("Account Number", "").strip().strip('"'),
            raw_action,
            _classify_action(raw_action),
            record.get("Symbol", "").strip(),
            record.get("Description", "").strip().strip('"'),
            record.get("Type", "").strip(),
            _parse_float(record.get("Quantity", "")),
            _parse_float(record.get("Price", "")),
            _parse_float(record.get("Amount", "")),
            record.get("Settlement Date", "").strip(),
        ))
        iso_dates.append(iso_date)

    if not rows:
        conn = get_connection(db_path)
        count: int = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
        conn.close()
        return count

    min_date = min(iso_dates)
    max_date = max(iso_dates)

    conn = get_connection(db_path)
    try:
        # Delete existing rows in the date range of this file (ISO dates sort
        # lexicographically). Each Fidelity CSV is authoritative for its range.
        conn.execute(
            "DELETE FROM fidelity_transactions WHERE run_date BETWEEN ? AND ?",
            (min_date, max_date),
        )

        # Insert all new rows — intra-day duplicates in the CSV are preserved
        # as-is (they represent real separate trades the CSV cannot distinguish
        # further).
        conn.executemany(
            """INSERT INTO fidelity_transactions
               (run_date, account, account_number, action, action_type, symbol,
                description, lot_type, quantity, price, amount, settlement_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
    finally:
        conn.close()

    return count
