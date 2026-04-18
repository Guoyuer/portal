"""Shared types and exceptions for the portfolio snapshot generator."""

from __future__ import annotations

import re
from typing import NotRequired, TypedDict

# ── Constants ───────────────────────────────────────────────────────────────

EQUITY_CATEGORIES = ["US Equity", "Non-US Equity"]

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


def parse_currency(val: str) -> float:
    """Parse a currency string like '$1,234.56' or '+$100.00' to float."""
    val = val.strip()
    if not val or val == "--":
        return 0.0
    return float(CURRENCY_RE.sub("", val))


# Alias for non-currency numeric parsing (same logic)
parse_float = parse_currency


# ── Config / Portfolio types ────────────────────────────────────────────────


class AssetInfo(TypedDict, total=False):
    category: str
    subtype: str


class QianjiAccountsConfig(TypedDict, total=False):
    fidelity_tracked: list[str]
    cny: list[str]
    credit: list[str]
    ticker_map: dict[str, str]


class Config(TypedDict):
    assets: dict[str, AssetInfo]
    weights: dict[str, float]
    order: list[str]
    aliases: dict[str, str]
    goal: float
    qianji_accounts: QianjiAccountsConfig
    # Optional: account number -> money market fund ticker. Unknown accounts fall back to FZFXX.
    fidelity_accounts: NotRequired[dict[str, str]]


class RawConfig(TypedDict, total=False):
    """Raw shape of config.json as it sits on disk. Matches the JSON keys
    directly (unlike :class:`Config`, which is the normalized form produced
    by :func:`etl.config.load_config` with renamed / defaulted fields).

    The pipeline mostly reads the raw form because it wants the original
    key names (``target_weights`` vs ``weights``, ``category_order`` vs
    ``order``) and a few raw-only fields (``retirement_income_categories``).
    All fields are optional via ``total=False``; callers use ``.get()``.
    """
    assets: dict[str, AssetInfo]
    target_weights: dict[str, float]
    category_order: list[str]
    aliases: dict[str, str]
    goal: float
    qianji_accounts: QianjiAccountsConfig
    fidelity_accounts: dict[str, str]
    retirement_income_categories: list[str]


# ── Record types (parsed from CSV/DB) ──────────────────────────────────────


class FidelityTransaction(TypedDict):
    date: str
    account: str
    action_type: str  # ACT_DEPOSIT, ACT_BUY, etc.
    symbol: str
    description: str
    lot_type: str  # "Cash", "Margin", "Shares", "Financing", or ""
    quantity: float
    price: float
    amount: float
    raw_action: str
    dedup_key: tuple[object, ...]


class QianjiRecord(TypedDict):
    id: str
    date: str
    category: str
    subcategory: str
    type: str  # "income", "expense", "transfer", "repayment"
    amount: float
    currency: str
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


# ── Exceptions ──────────────────────────────────────────────────────────────


class ConfigError(Exception):
    """Raised when the configuration file is missing, malformed, or invalid."""

