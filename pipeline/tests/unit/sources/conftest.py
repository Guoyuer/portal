"""Shared Fidelity-CSV scaffolding: header constants, row templates, writer."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

# Full 18-column Fidelity header (matches real export; used by test_fidelity_ingest).
FIDELITY_HEADER = (
    "Run Date,Account,Account Number,Action,Symbol,Description,Type,"
    "Exchange Quantity,Exchange Currency,Currency,Price,Quantity,"
    "Exchange Rate,Commission,Fees,Accrued Interest,Amount,Settlement Date"
)
# Stripped 11-column variant (test_fidelity — DictReader still finds what it needs).
FIDELITY_HEADER_SHORT = (
    "Run Date,Account,Account Number,Action,Symbol,Description,Type,"
    "Quantity,Price,Amount,Settlement Date"
)


def write_fidelity_csv(
    path: Path,
    rows: Sequence[str],
    header: str = FIDELITY_HEADER,
) -> Path:
    """Write a Fidelity Accounts_History shape CSV (two blank lines + header + rows)."""
    body = "\n\n" + header + "\n" + "\n".join(rows) + "\n"
    path.write_text(body, encoding="utf-8")
    return path


# Canonical 18-column rows — mutate only the fields a given test cares about.
ROW_AAPL = (
    '04/02/2026,"Taxable","Z29133576","YOU BOUGHT APPLE INC (AAPL) (Cash)",AAPL,'
    '"APPLE INC",Cash,0,,USD,252.56,3,0,,,,-757.68,04/06/2026'
)
ROW_GLDM = (
    '04/02/2026,"Taxable","Z29133576","YOU BOUGHT WORLD GOLD TR SPDR GLD MINIS (GLDM) (Cash)",GLDM,'
    '"WORLD GOLD TR SPDR GLD MINIS",Cash,0,,USD,91.13,10,0,,,,-911.3,04/06/2026'
)
ROW_EFT = (
    '04/02/2026,"Taxable","Z29133576","Electronic Funds Transfer Received (Cash)", ,'
    '"No Description",Cash,0,,USD,,0.000,0,,,,1500,'
)
