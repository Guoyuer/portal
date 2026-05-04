"""Small parsing helpers shared by broker ingest code.

Fidelity exports strict two-digit ``MM/DD/YYYY`` dates. Robinhood uses its own
source-local parser because its CSV accepts one-digit month/day values.
"""

from __future__ import annotations

import re

# Fidelity history CSVs have footer rows with narrative text that must be
# skipped silently before parsing the real transaction rows.
MMDDYYYY_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


# ── Public API ──────────────────────────────────────────────────────────────


def parse_mmddyyyy_date(raw: str, *, row_context: str = "") -> str:
    """Convert strict ``MM/DD/YYYY`` text to ISO ``YYYY-MM-DD``."""
    match = MMDDYYYY_RE.match(raw)
    if match is None:
        suffix = f" ({row_context})" if row_context else ""
        msg = f"Invalid US date {raw!r}: expected MM/DD/YYYY{suffix}"
        raise ValueError(msg)
    month, day, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def is_cusip(sym: str) -> bool:
    """True if ``sym`` looks like a CUSIP (8+ chars, leading digit).

    Fidelity reports Treasury holdings under their 9-char CUSIP (e.g.
    ``912796XA1``) rather than a ticker; we bucket all such entries into a
    single ``T-Bills`` line at face quantity. Shared with
    :mod:`etl.prices` which uses the same rule to skip CUSIPs from the
    daily-close fetch path (yfinance doesn't list them).
    """
    return bool(sym) and sym[0].isdigit() and len(sym) >= 8
