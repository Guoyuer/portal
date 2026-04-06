"""Middle layer: build structured ReportData from Portfolio + Config.

All renderers consume ReportData — they never touch Portfolio or Config directly.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

from .analysis import (
    aggregate_by_symbol,
    cat_value,
    get_tickers,
    group_by_subtype,
    pct,
)
from .config import classify_account
from .core.reconcile import CrossReconciliationData, cross_reconcile
from .types import (
    ACT_BUY,
    ACT_DEPOSIT,
    ACT_DIVIDEND,
    ACT_FOREIGN_TAX,
    ACT_INTEREST,
    ACT_REINVESTMENT,
    ACT_SELL,
    ACT_WITHDRAWAL,
    EQUITY_CATEGORIES,
    MIN_RECORDS_FOR_COMPLETE_MONTH,
    NON_EQUITY_CATEGORIES,
    QJ_EXPENSE,
    QJ_INCOME,
    QJ_REPAYMENT,
    QJ_TRANSFER,
    SUBTYPE_ORDER,
    TIER_CNY,
    TIER_CREDIT,
    TIER_FIDELITY,
    AccountBalance,
    ActivityData,
    AnnualCategoryTotal,
    AnnualSummary,
    BalanceSheetData,
    CashFlowData,
    CashFlowItem,
    CategoryData,
    ChartData,
    Config,
    FidelityTransaction,
    HoldingData,
    Portfolio,
    QianjiRecord,
    ReportData,
    ReportSources,
    SubtypeGroup,
)

log = logging.getLogger(__name__)


def _extract_date(filename: str) -> str:
    """Extract a human-readable date from a Fidelity CSV filename."""
    if m := re.search(r"Portfolio_Positions_([A-Za-z]+-\d+-\d+)", filename):
        try:
            return datetime.strptime(m.group(1), "%b-%d-%Y").strftime("%B %d, %Y")
        except ValueError:
            return m.group(1)
    return datetime.now().strftime("%B %d, %Y")


def _build_holding(ticker: str, portfolio: Portfolio, config: Config) -> HoldingData:
    """Build a HoldingData for a single ticker."""
    info = config["assets"].get(ticker, {})
    return HoldingData(
        ticker=ticker,
        lots=portfolio["counts"][ticker],
        value=portfolio["totals"][ticker],
        pct=pct(portfolio["totals"][ticker], portfolio["total"]),
        category=info.get("category", ""),
        subtype=info.get("subtype", ""),
        cost_basis=portfolio["cost_basis"].get(ticker, 0.0),
        gain_loss=portfolio["gain_loss"].get(ticker, 0.0),
        gain_loss_pct=portfolio["gain_loss_pct"].get(ticker, 0.0),
    )


def _build_category(
    category: str,
    portfolio: Portfolio,
    config: Config,
) -> CategoryData:
    """Build a CategoryData for one category."""
    tickers = get_tickers(portfolio, config, category)
    cat_value_total = sum(portfolio["totals"][t] for t in tickers)
    cat_lots = sum(portfolio["counts"][t] for t in tickers)
    cat_pct = pct(cat_value_total, portfolio["total"])
    target = config["weights"].get(category, 0)
    is_equity = category in EQUITY_CATEGORIES

    cat_data = CategoryData(
        name=category,
        value=cat_value_total,
        lots=cat_lots,
        pct=cat_pct,
        target=target,
        deviation=cat_pct - target,
        is_equity=is_equity,
    )

    if is_equity:
        groups = group_by_subtype(tickers, config)
        for grp_name in SUBTYPE_ORDER:
            if grp_name not in groups:
                continue
            grp_tickers = groups[grp_name]
            grp_value = sum(portfolio["totals"][t] for t in grp_tickers)
            grp_lots = sum(portfolio["counts"][t] for t in grp_tickers)
            cat_data.subtypes.append(
                SubtypeGroup(
                    name=grp_name,
                    holdings=[_build_holding(t, portfolio, config) for t in grp_tickers],
                    value=grp_value,
                    lots=grp_lots,
                    pct=pct(grp_value, portfolio["total"]),
                )
            )
    else:
        cat_data.holdings = [_build_holding(t, portfolio, config) for t in tickers]

    return cat_data


def _ordered_categories(
    portfolio: Portfolio,
    config: Config,
) -> tuple[list[str], list[str]]:
    """Return (equity_cats, non_equity_cats) in display order."""
    all_cats = {config["assets"].get(t, {}).get("category") for t in portfolio["totals"]}
    ordered = [c for c in config["order"] if c in all_cats]
    for c in sorted(all_cats, key=lambda x: cat_value(portfolio, config, x) if x else 0, reverse=True):
        if c and c not in ordered:
            ordered.append(c)
    return (
        [c for c in ordered if c in EQUITY_CATEGORIES],
        [c for c in ordered if c in NON_EQUITY_CATEGORIES],
    )


def _fidelity_date_to_ym(date_str: str) -> str:
    """Convert Fidelity MM/DD/YYYY to YYYY-MM."""
    try:
        return datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m")
    except ValueError:
        return ""


def _build_activity(transactions: list[FidelityTransaction], report_month: str) -> ActivityData:
    """Build ActivityData from Fidelity transaction records.

    If report_month is set (e.g., '2026-03'), only include transactions
    from that month. Otherwise include all.
    """
    deposits: list[FidelityTransaction] = []
    withdrawals: list[FidelityTransaction] = []
    buys: list[FidelityTransaction] = []
    sells: list[FidelityTransaction] = []
    dividends: list[FidelityTransaction] = []
    reinvestments_total = 0.0
    interest_total = 0.0
    foreign_tax_total = 0.0

    dates: list[str] = []

    for txn in transactions:
        if report_month and _fidelity_date_to_ym(txn["date"]) != report_month:
            continue
        action = txn["action_type"]
        dates.append(txn["date"])

        if action == ACT_DEPOSIT:
            deposits.append(txn)
        elif action == ACT_BUY:
            buys.append(txn)
        elif action == ACT_SELL:
            sells.append(txn)
        elif action == ACT_DIVIDEND:
            dividends.append(txn)
        elif action == ACT_REINVESTMENT:
            reinvestments_total += txn["amount"]
        elif action == ACT_INTEREST:
            interest_total += txn["amount"]
        elif action == ACT_FOREIGN_TAX:
            foreign_tax_total += txn["amount"]
        elif action == ACT_WITHDRAWAL:
            withdrawals.append(txn)

    sorted_dates = sorted(d for d in dates if d)
    period_start = sorted_dates[0] if sorted_dates else ""
    period_end = sorted_dates[-1] if sorted_dates else ""

    deposit_total = sum(t["amount"] for t in deposits)
    withdrawal_total = sum(t["amount"] for t in withdrawals)
    buy_total = sum(abs(t["amount"]) for t in buys)
    sell_total = sum(t["amount"] for t in sells)
    dividend_total = sum(t["amount"] for t in dividends)

    log.info("Activity %s\u2013%s: deposits=$%s buys=$%s sells=$%s dividends=$%s", period_start, period_end, f"{deposit_total:,.0f}", f"{buy_total:,.0f}", f"{sell_total:,.0f}", f"{dividend_total:,.0f}")
    return ActivityData(
        period_start=period_start,
        period_end=period_end,
        deposits=deposits,
        withdrawals=withdrawals,
        buys=buys,
        sells=sells,
        dividends=dividends,
        reinvestments_total=reinvestments_total,
        interest_total=interest_total,
        foreign_tax_total=foreign_tax_total,
        net_cash_in=deposit_total - withdrawal_total,
        net_deployed=buy_total - sell_total,
        net_passive=dividend_total + interest_total - abs(foreign_tax_total),
        buys_by_symbol=aggregate_by_symbol(buys),
        dividends_by_symbol=aggregate_by_symbol(dividends),
    )


# ── Account classification for Qianji ──────────────────────────────────────


def _fidelity_account_set(config: Config) -> frozenset[str]:
    """Return the set of Fidelity-tracked account names from config."""
    return frozenset(config["qianji_accounts"].get("fidelity_tracked", []))


def _build_balance_sheet_from_snapshot(
    portfolio: Portfolio,
    config: Config,
    snapshot: dict[str, Any],
) -> BalanceSheetData:
    """Build balance sheet from Qianji snapshot + flows + Fidelity positions.

    - Fidelity positions: authoritative for Fidelity-tracked investments
    - Qianji snapshot + flows: authoritative for bank, cash, CNY, credit cards
    - No double-counting: Fidelity accounts in Qianji are skipped
    """
    # Classify all accounts once, filter out Fidelity-tracked and zero balances
    cny_rate = snapshot["cny_rate"]
    account_tiers = {acct: classify_account(acct, config) for acct in snapshot.get("balances", {})}

    # Fidelity total = positions CSV total minus manual entries (those come from Qianji)
    ticker_map = config["qianji_accounts"].get("ticker_map", {})
    manual_tickers = set(ticker_map.values()) | {"CNY Assets"}
    fidelity_total = sum(v for t, v in portfolio["totals"].items() if t not in manual_tickers)

    # Group non-Fidelity accounts by tier
    cny_assets: list[AccountBalance] = []
    credit_cards: list[AccountBalance] = []
    cash_assets: list[AccountBalance] = []

    ticker_map_accounts = set(config["qianji_accounts"].get("ticker_map", {}).keys())
    for acct, bal in sorted(snapshot.get("balances", {}).items()):
        tier = account_tiers[acct]
        if tier == TIER_FIDELITY or acct in ticker_map_accounts or abs(bal) < 0.01:
            continue
        entry = AccountBalance(name=acct, balance=bal, currency="CNY" if tier == TIER_CNY else "USD")
        if tier == TIER_CNY:
            cny_assets.append(entry)
        elif tier == TIER_CREDIT:
            credit_cards.append(entry)
        else:
            cash_assets.append(entry)

    cny_total_usd = sum(a.balance for a in cny_assets) / cny_rate if cny_assets else 0
    cash_total = sum(a.balance for a in cash_assets)
    total_liabilities = abs(sum(a.balance for a in credit_cards if a.balance < 0))
    # Portfolio total already includes all assets (Fidelity + manual entries)
    total_assets = portfolio["total"]
    net_worth = total_assets - total_liabilities

    log.info("Balance sheet: assets=$%s (portfolio=$%s), liabilities=$%s, net_worth=$%s", f"{total_assets:,.0f}", f"{portfolio['total']:,.0f}", f"{total_liabilities:,.0f}", f"{net_worth:,.0f}")
    return BalanceSheetData(
        investment_total=fidelity_total,
        accounts=cash_assets + cny_assets,
        accounts_total=cash_total + cny_total_usd,
        credit_cards=credit_cards,
        total_liabilities=total_liabilities,
        total_assets=total_assets,
        net_worth=net_worth,
    )


def _latest_complete_month(cashflow: list[QianjiRecord]) -> str:
    """Return 'YYYY-MM' for the most recent COMPLETE month.

    If the latest record is in the current month (partial data), use the
    previous month instead. A partial month has misleading income/expense
    totals and savings rate.
    """
    months: set[str] = set()
    for record in cashflow:
        date_str = record["date"][:7]
        if len(date_str) == 7 and date_str[4] == "-":
            months.add(date_str)
    if not months:
        return ""
    sorted_months = sorted(months)
    latest = sorted_months[-1]
    # If latest month has < 25 records, it's probably partial — use previous
    latest_count = sum(1 for r in cashflow if r["date"][:7] == latest)
    if latest_count < MIN_RECORDS_FOR_COMPLETE_MONTH and len(sorted_months) >= 2:
        return sorted_months[-2]
    return latest


def _build_cashflow(cashflow: list[QianjiRecord], config: Config, report_month: str) -> CashFlowData:
    """Build CashFlowData from Qianji cashflow records for the given month."""
    target_month = report_month or _latest_complete_month(cashflow)
    fidelity_accounts = _fidelity_account_set(config)

    income_by_cat: dict[str, float] = defaultdict(float)
    income_counts: dict[str, int] = defaultdict(int)
    expense_by_cat: dict[str, float] = defaultdict(float)
    expense_counts: dict[str, int] = defaultdict(int)
    invested = 0.0
    credit_card_payments = 0.0

    for record in cashflow:
        date_str = record["date"]
        # Filter to most recent month only
        if target_month and not date_str.startswith(target_month):
            continue

        record_type = record["type"]
        amount = record["amount"]
        category = record["category"]

        if record_type == QJ_INCOME:
            income_by_cat[category] += amount
            income_counts[category] += 1
        elif record_type == QJ_EXPENSE:
            expense_by_cat[category] += amount
            expense_counts[category] += 1
        elif record_type == QJ_TRANSFER:
            if record["account_to"] in fidelity_accounts:
                invested += amount
        elif record_type == QJ_REPAYMENT:
            credit_card_payments += amount

    total_income = sum(income_by_cat.values())
    total_expenses = sum(expense_by_cat.values())
    net_cashflow = total_income - total_expenses

    income_items = sorted(
        [CashFlowItem(category=cat, amount=amt, count=income_counts[cat]) for cat, amt in income_by_cat.items()],
        key=lambda x: x.amount,
        reverse=True,
    )
    expense_items = sorted(
        [CashFlowItem(category=cat, amount=amt, count=expense_counts[cat]) for cat, amt in expense_by_cat.items()],
        key=lambda x: x.amount,
        reverse=True,
    )

    savings_rate = (net_cashflow / total_income * 100) if total_income > 0 else 0.0

    # Take-home savings rate: exclude pre-tax retirement contributions (401k)
    pretax_income = sum(amt for cat, amt in income_by_cat.items() if "401" in cat.lower())
    takehome_income = total_income - pretax_income
    takehome_savings_rate = ((takehome_income - total_expenses) / takehome_income * 100) if takehome_income > 0 else 0.0

    if target_month:
        try:
            period = datetime.strptime(target_month, "%Y-%m").strftime("%B %Y")
        except ValueError:
            period = target_month
    else:
        period = "Unknown"

    log.info("Cashflow %s: income=$%s expenses=$%s savings=%.1f%% invested=$%s", period, f"{total_income:,.0f}", f"{total_expenses:,.0f}", savings_rate, f"{invested:,.0f}")
    return CashFlowData(
        period=period,
        income_items=income_items,
        total_income=total_income,
        expense_items=expense_items,
        total_expenses=total_expenses,
        net_cashflow=net_cashflow,
        invested=invested,
        credit_card_payments=credit_card_payments,
        savings_rate=savings_rate,
        takehome_savings_rate=takehome_savings_rate,
    )


def _build_cross_reconciliation(
    transactions: list[FidelityTransaction],
    cashflow: list[QianjiRecord],
    config: Config,
) -> CrossReconciliationData:
    """Build CrossReconciliationData by matching Qianji transfers to Fidelity deposits.

    Only compares transfers within the Fidelity date range to avoid misleading
    unmatched counts from Qianji records outside the history window.
    """
    fidelity_accts = _fidelity_account_set(config)

    # First, collect Fidelity deposits and determine the date range
    # Single pass: collect deposits and all dates
    fidelity_deposits: list[dict[str, Any]] = []
    all_dates: list[str] = []
    skipped = 0
    for txn in transactions:
        try:
            date_str = datetime.strptime(txn["date"], "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            skipped += 1
            continue
        all_dates.append(date_str)
        if txn["action_type"] == ACT_DEPOSIT:
            fidelity_deposits.append({"date": date_str, "amount": txn["amount"], "description": txn["description"]})
    if skipped:
        log.warning("Skipped %d transactions with unparseable dates", skipped)
    fi_min = min(all_dates) if all_dates else ""
    fi_max = max(all_dates) if all_dates else ""

    # Filter Qianji transfers to the Fidelity date range only
    qianji_transfers: list[dict[str, Any]] = []
    for record in cashflow:
        if record["type"] == QJ_TRANSFER and record["account_to"] in fidelity_accts:
            date_str = record["date"][:10]
            # Only include transfers within the Fidelity history window
            if fi_min and fi_max and fi_min <= date_str <= fi_max:
                qianji_transfers.append(
                    {
                        "date": date_str,
                        "amount": record["amount"],
                        "note": record["note"],
                    }
                )

    result = cross_reconcile(qianji_transfers, fidelity_deposits)
    log.info("Cross-reconciliation: %d matched, %d unmatched Qianji, %d unmatched Fidelity", len(result.matched), len(result.unmatched_qianji), len(result.unmatched_fidelity))
    return result


def _build_annual_summary(
    cashflow: list[QianjiRecord],
    config: Config,
    report_month: str = "",
) -> AnnualSummary | None:
    """Build annual expense/income summary for the given year.

    Uses the year from report_month (e.g. '2026-03') if provided,
    otherwise falls back to the current year.
    """
    if report_month and len(report_month) >= 4:
        year = int(report_month[:4])
    else:
        year = datetime.now().year

    expense_by_cat: dict[str, float] = defaultdict(float)
    expense_counts: dict[str, int] = defaultdict(int)
    total_income = 0.0
    pretax_income = 0.0
    fidelity_tracked = frozenset(config["qianji_accounts"].get("fidelity_tracked", []))

    for record in cashflow:
        record_year = int(record["date"][:4])
        if record_year != year:
            continue
        if record["type"] == QJ_EXPENSE:
            cat = record["category"] or "Other"
            expense_by_cat[cat] += record["amount"]
            expense_counts[cat] += 1
        elif record["type"] == QJ_INCOME:
            cat = record["category"] or "Other"
            total_income += record["amount"]
            if "401" in cat.lower():
                pretax_income += record["amount"]
        elif record["type"] == QJ_TRANSFER and record["account_to"] in fidelity_tracked:
            pass  # investment transfers, not expense

    if not expense_by_cat:
        return None

    total_expenses = sum(expense_by_cat.values())
    log.info("Annual %d: expenses=$%s (%d categories) income=$%s", year, f"{total_expenses:,.0f}", len(expense_by_cat), f"{total_income:,.0f}")
    items = sorted(
        [AnnualCategoryTotal(category=cat, amount=amt, count=expense_counts[cat]) for cat, amt in expense_by_cat.items()],
        key=lambda x: x.amount,
        reverse=True,
    )

    takehome_income = total_income - pretax_income
    takehome_savings_rate = ((takehome_income - total_expenses) / takehome_income * 100) if takehome_income > 0 else 0.0

    return AnnualSummary(
        year=year,
        expense_by_category=items,
        total_expenses=total_expenses,
        total_income=total_income,
        takehome_savings_rate=takehome_savings_rate,
    )


def build_report(
    portfolio: Portfolio,
    config: Config,
    filename: str,
    *,
    transactions: list[FidelityTransaction] | None = None,
    cashflow: list[QianjiRecord] | None = None,
    balance_snapshot: dict[str, Any] | None = None,
    report_month: str = "",
    sources: ReportSources | None = None,
    chart_data: ChartData | None = None,
    prev_totals: dict[str, float] | None = None,
    prev_date: str = "",
) -> ReportData:
    """Build a complete ReportData from raw portfolio and config."""
    log.info("Building report for %s: $%s across %d tickers", filename, f"{portfolio['total']:,.2f}", len(portfolio["totals"]))
    s = sources or ReportSources()
    report_date = _extract_date(filename)

    eq_names, non_eq_names = _ordered_categories(portfolio, config)

    equity_categories = [_build_category(c, portfolio, config) for c in eq_names]
    non_equity_categories = [_build_category(c, portfolio, config) for c in non_eq_names]

    goal = config["goal"]

    # Determine report month — both Activity and Cash Flow use the same period
    if not report_month:
        report_month = _latest_complete_month(cashflow) if cashflow else ""

    # Build optional sections from available data
    activity = _build_activity(transactions, report_month) if transactions else None

    balance_sheet = _build_balance_sheet_from_snapshot(portfolio, config, balance_snapshot) if balance_snapshot else None

    cashflow_data = _build_cashflow(cashflow, config, report_month) if cashflow else None
    annual_summary = _build_annual_summary(cashflow, config, report_month) if cashflow else None
    cross_reconciliation_data = (
        _build_cross_reconciliation(transactions, cashflow, config) if transactions and cashflow else None
    )

    # Portfolio reconciliation: break down value changes by tier
    reconciliation_data = None
    if prev_totals and transactions:
        from .core.reconcile import portfolio_reconcile

        recon = portfolio_reconcile(
            current=portfolio["totals"],
            previous=prev_totals,
            transactions=transactions,
            config=config,
        )
        recon.prev_date = prev_date
        recon.curr_date = report_date
        reconciliation_data = recon

    log.info("Report built: %s equity cats, %s non-equity cats, reconciliation=%s", len(equity_categories), len(non_equity_categories), reconciliation_data is not None)
    return ReportData(
        date=report_date,
        total=portfolio["total"],
        total_lots=sum(portfolio["counts"].values()),
        goal=goal,
        goal_pct=pct(balance_sheet.net_worth if balance_sheet else portfolio["total"], goal) if goal > 0 else 0,
        equity_categories=equity_categories,
        non_equity_categories=non_equity_categories,
        activity=activity,
        reconciliation=reconciliation_data,
        balance_sheet=balance_sheet,
        cashflow=cashflow_data,
        cross_reconciliation=cross_reconciliation_data,
        chart_data=chart_data,
        annual_summary=annual_summary,
        market=s.market,
        holdings_detail=s.holdings_detail,
    )
