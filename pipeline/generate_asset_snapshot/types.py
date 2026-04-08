"""Shared types and exceptions for the portfolio snapshot generator."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from .core.reconcile import CrossReconciliationData, ReconciliationData

# ── Constants ───────────────────────────────────────────────────────────────

DEFAULT_CNY_RATE = 6.88

EQUITY_CATEGORIES = ["US Equity", "Non-US Equity"]
NON_EQUITY_CATEGORIES = ["Crypto", "Safe Net"]
SUBTYPE_ORDER = ["broad", "growth", "other"]

MIN_RECORDS_FOR_COMPLETE_MONTH = 25  # fewer records → likely partial month

# Fidelity transaction action types (used in fidelity_history.py, report.py, reconcile.py)
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

# Account classification tiers (returned by classify_account)
TIER_FIDELITY = "fidelity"
TIER_CREDIT = "credit"
TIER_CNY = "cny"
TIER_CASH = "cash"

CURRENCY_RE = re.compile(r"[^0-9.-]")


def parse_currency(val: str) -> float:
    """Parse a currency string like '$1,234.56' or '+$100.00' to float."""
    val = val.strip()
    if not val or val == "--":
        return 0.0
    return float(CURRENCY_RE.sub("", val))


# ── Config / Portfolio types ────────────────────────────────────────────────


class AssetInfo(TypedDict, total=False):
    category: str
    subtype: str
    source: str  # "fidelity", "linked", or "manual" (for portfolio reconciliation)


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


class Portfolio(TypedDict):
    totals: dict[str, float]
    counts: dict[str, int]
    total: float
    cost_basis: dict[str, float]  # ticker → total cost basis
    gain_loss: dict[str, float]  # ticker → total gain/loss $
    gain_loss_pct: dict[str, float]  # ticker → total gain/loss %


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


# ── Exceptions ──────────────────────────────────────────────────────────────


class ConfigError(Exception):
    """Raised when the configuration file is missing, malformed, or invalid."""


class PortfolioError(Exception):
    """Raised when portfolio CSV cannot be loaded or contains invalid data."""


# ── Report data (middle layer) ──────────────────────────────────────────────


@dataclass
class HoldingData:
    ticker: str
    lots: int
    value: float
    pct: float
    category: str
    subtype: str  # "broad", "growth", "other", or "" for non-equity
    cost_basis: float = 0.0
    gain_loss: float = 0.0
    gain_loss_pct: float = 0.0


@dataclass
class SubtypeGroup:
    name: str  # "broad", "growth", "other"
    holdings: list[HoldingData]
    value: float
    lots: int
    pct: float


@dataclass
class CategoryData:
    name: str
    value: float
    lots: int
    pct: float
    target: float  # target weight %
    deviation: float  # actual% - target%
    is_equity: bool
    subtypes: list[SubtypeGroup] = field(default_factory=list)
    holdings: list[HoldingData] = field(default_factory=list)  # flat list for non-equity


# ── Extended report sections ────────────────────────────────────────────────


@dataclass
class ActivityData:
    """Investment activity from Fidelity History CSV."""

    period_start: str  # "2026-03-12"
    period_end: str  # "2026-04-02"
    reinvestments_total: float
    interest_total: float
    foreign_tax_total: float
    net_cash_in: float  # deposits - withdrawals
    net_deployed: float  # buys - sells
    net_passive: float  # dividends + interest - foreign_tax
    buys_by_symbol: list[tuple[str, int, float]]
    dividends_by_symbol: list[tuple[str, int, float]]


@dataclass
class BalanceSheetData:
    """Personal balance sheet from Qianji + Fidelity."""

    total_assets: float
    total_liabilities: float
    net_worth: float


@dataclass
class CashFlowItem:
    """Single line in cash flow statement."""

    category: str  # "Meals", "Housing", "Salary"
    amount: float
    count: int  # number of transactions


@dataclass
class CashFlowData:
    """Monthly cash flow statement from Qianji."""

    period: str  # "March 2026"
    income_items: list[CashFlowItem]
    total_income: float
    expense_items: list[CashFlowItem]  # sorted by amount descending
    total_expenses: float
    net_cashflow: float
    invested: float  # transfers to investment accounts
    credit_card_payments: float  # repayments
    savings_rate: float  # (total_income - total_expenses) / total_income * 100 (gross, includes 401k)
    takehome_savings_rate: float  # excludes pre-tax retirement contributions from income


@dataclass
class IndexReturn:
    """Return data for a market index."""

    ticker: str
    name: str  # "S&P 500", "NASDAQ"
    month_return: float
    ytd_return: float
    current: float
    sparkline: list[float] | None = None  # daily closes for sparkline chart
    high_52w: float | None = None
    low_52w: float | None = None


@dataclass
class MarketData:
    """Market context from external APIs."""

    indices: list[IndexReturn]
    fed_rate: float | None = None
    treasury_10y: float | None = None
    cpi: float | None = None
    unemployment: float | None = None
    vix: float | None = None
    dxy: float | None = None
    usd_cny: float | None = None
    gold_return: float | None = None
    btc_return: float | None = None
    portfolio_month_return: float | None = None


@dataclass
class StockDetail:
    """Per-stock detail for holdings deep dive."""

    ticker: str
    month_return: float
    start_value: float
    end_value: float
    pe_ratio: float | None
    market_cap: float | None
    high_52w: float | None
    low_52w: float | None
    vs_high: float | None  # current / 52w_high - 1
    next_earnings: str | None  # "Apr 24 (Thu)"


@dataclass
class HoldingsDetailData:
    """Holdings deep dive from Yahoo Finance."""

    top_performers: list[StockDetail]  # sorted by month_return desc, top 5
    bottom_performers: list[StockDetail]  # sorted by month_return asc, top 5
    upcoming_earnings: list[StockDetail]  # stocks with earnings in next 30 days


@dataclass
class SnapshotPoint:
    """Single historical portfolio snapshot for trend charts."""

    date: str  # "2025-11-07"
    total: float


@dataclass
class MonthlyFlowPoint:
    """Single month's income/expense totals for trend charts."""

    month: str  # "2025-11"
    income: float
    expenses: float
    savings_rate: float  # (income - expenses) / income * 100


@dataclass
class AnnualCategoryTotal:
    """Single category's annual total."""

    category: str
    amount: float
    count: int


@dataclass
class AnnualSummary:
    """Annual income/expense summary by category."""

    year: int
    expense_by_category: list[AnnualCategoryTotal]
    total_expenses: float
    total_income: float
    takehome_savings_rate: float = 0.0


@dataclass
class ChartData:
    """Chart data computed from historical sources. Optional on ReportData."""

    net_worth_trend: list[SnapshotPoint] = field(default_factory=list)
    monthly_flows: list[MonthlyFlowPoint] = field(default_factory=list)


@dataclass
class ReportSources:
    """Optional data sources passed through to ReportData."""

    market: MarketData | None = None
    holdings_detail: HoldingsDetailData | None = None


# ── Full ReportData ─────────────────────────────────────────────────────────


@dataclass
class ReportData:
    # Core (always present)
    date: str
    total: float
    total_lots: int
    goal: float
    goal_pct: float
    equity_categories: list[CategoryData]
    non_equity_categories: list[CategoryData]

    # Investment activity (if Fidelity history available)
    activity: ActivityData | None = None

    # Portfolio reconciliation (if previous snapshot exists)
    reconciliation: ReconciliationData | None = None

    # Personal finance (if Qianji available)
    balance_sheet: BalanceSheetData | None = None
    cashflow: CashFlowData | None = None
    cross_reconciliation: CrossReconciliationData | None = None

    # Market context (if APIs available)
    market: MarketData | None = None
    holdings_detail: HoldingsDetailData | None = None

    # Charts (if historical data available)
    chart_data: ChartData | None = None

    # Annual summary (if Qianji data spans multiple months)
    annual_summary: AnnualSummary | None = None
