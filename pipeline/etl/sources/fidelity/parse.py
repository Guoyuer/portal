"""Fidelity Accounts_History CSV → ``fidelity_transactions`` rows.

Owns:
  - Action classification (verbose Fidelity ``Action`` strings →
    ``ACT_*`` labels for the ``action_type`` column → normalized
    :class:`ActionKind` for the ``action_kind`` column).
  - Header detection + chronological ordering (``_csv_earliest_date``).
  - Canonical ingest across overlapping downloaded CSV exports.
"""
from __future__ import annotations

import csv
import logging
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from etl.db import get_connection
from etl.parsing import STRICT_US_DATE_RE, parse_us_date
from etl.sources._types import ActionKind
from etl.types import (
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
)
from etl.types import parse_currency as _parse_float

log = logging.getLogger(__name__)

TABLE = "fidelity_transactions"
FidelityTxnRow = tuple[str, str, str, str, str, str, str, float, float, float]


# ── Action classification ───────────────────────────────────────────────────

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


# Fidelity's canonical action_type constants → normalized :class:`ActionKind`.
# Kept in sync with ``_classify_action`` so Fidelity's classification rules
# stay the single source of truth.
_ACTION_TYPE_TO_KIND: dict[str, ActionKind] = {
    ACT_BUY: ActionKind.BUY,
    ACT_SELL: ActionKind.SELL,
    ACT_DIVIDEND: ActionKind.DIVIDEND,
    ACT_REINVESTMENT: ActionKind.REINVESTMENT,
    ACT_DEPOSIT: ActionKind.DEPOSIT,
    ACT_WITHDRAWAL: ActionKind.WITHDRAWAL,
    # Internal retirement/account transfers all map to TRANSFER — they move
    # cash between accounts but neither deposit nor withdraw at the portfolio
    # level. Rollovers, partial recharacterizations, Roth conversions.
    ACT_TRANSFER: ActionKind.TRANSFER,
    ACT_ROTH_CONVERSION: ActionKind.TRANSFER,
    # IRA cash contributions function as deposits into the retirement account.
    ACT_IRA_CONTRIBUTION: ActionKind.DEPOSIT,
    # Position-prefix-but-not-cost-basis-impacting actions. The primitive's
    # widened vocabulary handles qty updates for each one (``qty += q``)
    # while leaving cost basis alone — mirrors the legacy
    # ``POSITION_PREFIXES`` behaviour.
    #
    # ``ACT_DISTRIBUTION`` is also how Fidelity CSVs encode stock splits:
    # a 3:1 on SCHD arrives as ``DISTRIBUTION SCHWAB US DIVIDEND EQUITY
    # ETF (SCHD)`` with ``quantity = pre_split_qty × 2`` and ``price = 0``.
    # The qty-only, cost-basis-preserving handling is correct for splits
    # (no cash moves; per-share basis drops proportionally). See
    # :class:`etl.sources.ActionKind` for the full invariant.
    ACT_DISTRIBUTION: ActionKind.DISTRIBUTION,
    ACT_REDEMPTION: ActionKind.REDEMPTION,
    ACT_EXCHANGE: ActionKind.EXCHANGE,
    # Non-position, non-cashflow pass-throughs.
    ACT_INTEREST: ActionKind.OTHER,
    ACT_FOREIGN_TAX: ActionKind.OTHER,
    ACT_LENDING: ActionKind.OTHER,
    ACT_COLLATERAL: ActionKind.OTHER,
    ACT_OTHER: ActionKind.OTHER,
}


def classify_fidelity_action(raw_action: str) -> ActionKind:
    """Map a verbose Fidelity action string to the normalized :class:`ActionKind`.

    Unknown action types fall through to :attr:`ActionKind.OTHER`.
    """
    return _ACTION_TYPE_TO_KIND.get(_classify_action(raw_action), ActionKind.OTHER)


# ── CSV → in-memory transaction list ──────────────────────────────────────


_CSV_DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")


def _csv_earliest_date(path: Path) -> str:
    """Return earliest ``YYYYMMDD`` date in a CSV for chronological sorting.

    Raw Fidelity CSVs carry ``MM/DD/YYYY`` dates; convert to ``YYYYMMDD`` for
    a lexicographically sortable key so ``sorted(..., key=_csv_earliest_date)``
    picks the oldest-first ordering. Paths with no recognizable dates sort
    last (very large sentinel).
    """
    text = path.read_text(encoding="utf-8-sig")
    dates: list[str] = _CSV_DATE_RE.findall(text)
    if not dates:
        return "99999999"
    return min(d[6:10] + d[0:2] + d[3:5] for d in dates)


# ── DB ingest ──────────────────────────────────────────────────────────────


def _parse_csv_rows(csv_path: Path) -> list[FidelityTxnRow]:
    """Parse one Fidelity CSV into normalized transaction rows."""
    text = csv_path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()

    # Find the header line (starts with "Run Date").
    header_idx = -1
    for i, line in enumerate(lines):
        if line.strip().startswith("Run Date"):
            header_idx = i
            break
    if header_idx == -1:
        msg = f"No header row found in {csv_path}"
        raise ValueError(msg)

    reader = csv.DictReader(lines[header_idx:])
    rows: list[FidelityTxnRow] = []

    # DictReader consumes the header, so the first data row is file
    # line header_idx + 2.
    for offset, record in enumerate(reader):
        run_date_raw = record.get("Run Date", "").strip()
        # Skip blank rows and footer/disclaimer text; only rows shaped
        # like a Fidelity date participate in ingestion.
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
            record.get("Account Number", "").strip().strip('"'),
            raw_action,
            _classify_action(raw_action),
            classify_fidelity_action(raw_action).value,
            record.get("Symbol", "").strip(),
            record.get("Type", "").strip(),
            _parse_float(record.get("Quantity", "")),
            _parse_float(record.get("Price", "")),
            _parse_float(record.get("Amount", "")),
        ))

    return rows


def _canonical_rows(csv_paths: Iterable[Path]) -> list[FidelityTxnRow]:
    """Merge CSV rows while dropping duplicate observations across files."""
    rows: list[FidelityTxnRow] = []
    seen: set[tuple[FidelityTxnRow, int]] = set()
    for csv_path in csv_paths:
        occurrences: Counter[FidelityTxnRow] = Counter()
        for row in _parse_csv_rows(csv_path):
            occurrences[row] += 1
            token = (row, occurrences[row])
            if token in seen:
                continue
            seen.add(token)
            rows.append(row)
    return rows


def ingest_csvs(db_path: Path, csv_paths: Iterable[Path]) -> int:
    """Rebuild Fidelity transactions from the canonical union of CSV exports.

    Fidelity history exports can overlap and be partial on boundary dates.
    Treating each CSV as authoritative for its whole min/max range can delete
    valid rows from another export. The safer invariant is to retain every row
    observed in any export, while de-duplicating repeated observations across
    files. Same-file duplicate rows are preserved because Fidelity can emit
    indistinguishable legitimate trades.
    """
    ordered_csvs = sorted(csv_paths, key=lambda path: (_csv_earliest_date(path), path.name))
    rows = _canonical_rows(ordered_csvs)

    conn = get_connection(db_path)
    try:
        conn.execute(f"DELETE FROM {TABLE}")  # noqa: S608 — TABLE is a module-level constant
        if rows:
            conn.executemany(
                f"INSERT INTO {TABLE} "  # noqa: S608 — TABLE is a module-level constant
                "(run_date, account_number, action, action_type, action_kind, "
                "symbol, lot_type, quantity, price, amount) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        conn.commit()

        count: int = conn.execute(
            f"SELECT COUNT(*) FROM {TABLE}"  # noqa: S608
        ).fetchone()[0]
    finally:
        conn.close()

    return count
