"""Shared input parsing helpers for broker CSVs.

Both Fidelity (``MM/DD/YYYY``) and Robinhood (``M/D/YYYY``) export US-format
dates. ``parse_us_date`` is the single entry point; use ``strict=True`` when
the source guarantees two-digit components (Fidelity), ``strict=False``
otherwise (Robinhood).

``read_csv_rows`` is a BOM-tolerant one-liner that every broker-CSV reader
shares — eliminates the ``read_text("utf-8-sig") → splitlines → DictReader``
dance at each call site.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

# ── Patterns ────────────────────────────────────────────────────────────────

# Exported for callers that need a "does this row look like a Fidelity date?"
# guard before invoking parse_us_date. (Fidelity history CSVs have footer rows
# with narrative text that must be skipped silently.)
STRICT_US_DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_LOOSE_US_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


# ── Public API ──────────────────────────────────────────────────────────────


def parse_us_date(raw: str, *, strict: bool = False, row_context: str = "") -> str:
    """Convert a US-format date string to ISO ``YYYY-MM-DD``.

    Parameters
    ----------
    raw
        Input string. Leading/trailing whitespace is preserved as-is so callers
        pre-strip it only when they want that semantic.
    strict
        When ``True``, demand two-digit month and day (Fidelity export format).
        When ``False``, accept one-digit month/day (Robinhood format).
    row_context
        Appended to the ValueError message for traceability.

    Raises
    ------
    ValueError
        If ``raw`` does not match the requested pattern.
    """
    pattern = STRICT_US_DATE_RE if strict else _LOOSE_US_DATE_RE
    match = pattern.match(raw)
    if match is None:
        suffix = f" ({row_context})" if row_context else ""
        expected = "MM/DD/YYYY" if strict else "M/D/YYYY"
        msg = f"Invalid US date {raw!r}: expected {expected}{suffix}"
        raise ValueError(msg)
    month, day, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    """Read a UTF-8 BOM-tolerant CSV into a list of ``{header: cell}`` dicts.

    Used by every broker-CSV reader (Fidelity, Robinhood, positions-CSV
    verifier). Always materialises the full row list so callers can iterate
    multiple times; CSVs in this codebase are small enough (thousands of
    rows) that streaming buys nothing.
    """
    return list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))


def is_cusip(sym: str) -> bool:
    """True if ``sym`` looks like a CUSIP (8+ chars, leading digit).

    Fidelity reports Treasury holdings under their 9-char CUSIP (e.g.
    ``912796XA1``) rather than a ticker; we bucket all such entries into a
    single ``T-Bills`` line at face quantity. Shared with
    :mod:`etl.prices` which uses the same rule to skip CUSIPs from the
    daily-close fetch path (yfinance doesn't list them).
    """
    return bool(sym) and sym[0].isdigit() and len(sym) >= 8
