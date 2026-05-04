"""Shared types and exceptions for the portfolio snapshot generator."""

from __future__ import annotations

import re
from typing import TypedDict

# ── Constants ───────────────────────────────────────────────────────────────

# Trading-day lookback windows (US equity market)
TRADING_DAYS_MONTH = 23  # index offset for ~22 trading days back (~1 month)
TRADING_DAYS_YEAR = 252  # ~1 year of US trading days (used for 52-week windows & sparklines)

# Fidelity transaction action types (used in etl/sources/fidelity.py)
ACT_DEPOSIT = "deposit"
ACT_BUY = "buy"
ACT_SELL = "sell"
ACT_DIVIDEND = "dividend"
ACT_REINVESTMENT = "reinvestment"
ACT_IRA_CONTRIBUTION = "ira_contribution"
ACT_ROTH_CONVERSION = "roth_conversion"
ACT_TRANSFER = "transfer"
ACT_INTEREST = "interest"
ACT_FOREIGN_TAX = "foreign_tax"
ACT_LENDING = "lending"
ACT_COLLATERAL = "collateral"
ACT_WITHDRAWAL = "withdrawal"
ACT_REDEMPTION = "redemption"
ACT_DISTRIBUTION = "distribution"
ACT_EXCHANGE = "exchange"
ACT_OTHER = "other"

# Qianji record types
QJ_INCOME = "income"
QJ_EXPENSE = "expense"
QJ_TRANSFER = "transfer"
QJ_REPAYMENT = "repayment"

# Strip everything except digits, dot and minus. Fidelity / Robinhood CSVs
# format currency as "$1,234.56", "+$100.00", "($50.00)" — drop the symbol
# and grouping comma, keep the sign.
CURRENCY_RE = re.compile(r"[^0-9.-]")

# Robinhood encodes negatives as parentheses — e.g. "($50.00)" means -50.00.
# Fidelity uses explicit minus signs, so this branch only fires on Robinhood
# rows. Detecting parens BEFORE the digit-strip preserves the sign that
# ``CURRENCY_RE`` would otherwise discard along with the paren glyphs.
_PARENS_NEG_RE = re.compile(r"^\s*\(\s*[^)]*\)\s*$")


def parse_currency(val: str) -> float:
    """Parse a currency string like '$1,234.56', '+$100.00', or '($50.00)' to float.

    Recognizes parentheses-wrapped values as negatives (Robinhood convention).
    """
    val = val.strip()
    if not val or val == "--":
        return 0.0
    negate = bool(_PARENS_NEG_RE.match(val))
    n = float(CURRENCY_RE.sub("", val))
    return -n if negate else n


# ── Config / Portfolio types ────────────────────────────────────────────────


class AssetInfo(TypedDict, total=False):
    category: str
    subtype: str


class QianjiAccountsConfig(TypedDict, total=False):
    ticker_map: dict[str, str]


class RawConfig(TypedDict, total=False):
    """Raw shape of config.json as it sits on disk.

    Matches the JSON keys directly (``target_weights``, ``category_order``,
    ``retirement_income_categories`` etc.). Per-source tuning keys live here
    too so the whole config flows as a single typed dict. All fields are
    optional via ``total=False``; callers use ``.get()``.
    """
    # Core JSON keys
    assets: dict[str, AssetInfo]
    target_weights: dict[str, float]
    category_order: list[str]
    qianji_accounts: QianjiAccountsConfig
    fidelity_accounts: dict[str, str]
    retirement_income_categories: list[str]
    # Per-source tuning keys
    mutual_funds: list[str]
    empower_cusip_map: dict[str, str]


# ── Record types (parsed from source DB / computed ETL rows) ────────────────


class QianjiRecord(TypedDict):
    date: str
    category: str
    type: str  # "income", "expense", "transfer", "repayment"
    amount: float
    account_from: str
    account_to: str
    note: str


class TickerDetail(TypedDict):
    """One ticker's contribution on a given day (child of AllocationRow)."""
    ticker: str
    value: float
    category: str
    subtype: str
    cost_basis: float
    gain_loss: float
    gain_loss_pct: float


class AllocationRow(TypedDict):
    """One day's full portfolio allocation, as produced by
    :func:`etl.allocation.step_one_day` and consumed by
    :func:`etl.db.upsert_daily_rows`."""
    date: str
    total: float
    us_equity: float
    non_us_equity: float
    crypto: float
    safe_net: float
    liabilities: float
    tickers: list[TickerDetail]
