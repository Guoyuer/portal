"""HTML renderer — generates a self-contained HTML report from ReportData.

Suitable for saving to a file, embedding in an email body, or serving via HTTP.

IMPORTANT: This renderer and terminal.py must stay in sync — same section order,
same truncation rules (top 5 + others), same collapsing thresholds (< 1%),
same $ formatting. When changing one, update the other.
All CSS is inlined for maximum email client compatibility.
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from ..types import (
    ACTIVITY_TOP_SYMBOLS,
    MAJOR_EXPENSE_THRESHOLD,
    MIN_HOLDING_PCT,
    AccountBalance,
    ActivityData,
    BalanceSheetData,
    CashFlowData,
    CategoryData,
    ContributionData,
    HoldingData,
    HoldingsDetailData,
    MarketData,
    ReportData,
)
from . import svg as _svg

if TYPE_CHECKING:
    from ..core.reconcile import CrossReconciliationData

# ── Colour and style constants ────────────────────────────────────────────────

_CLR_DARK = "#1a1a2e"
_CLR_SECTION = "#16213e"
_CLR_ACCENT = "#0f3460"
_CLR_RED = "#e94560"
_CLR_GREEN = "#27ae60"
_CLR_ROW_ALT = "#f8f9fa"
_CLR_ROW_EVEN = "#ffffff"

_FONT_STACK = "Arial, Helvetica, sans-serif"


# ── Formatting helpers ────────────────────────────────────────────────────────


def _fmt_currency(val: float) -> str:
    """Format as $X,XXX.XX."""
    if val < 0:
        return f"-${abs(val):,.2f}"
    return f"${val:,.2f}"


def _fmt_pct(val: float, signed: bool = True) -> str:
    """Format as +X.X% or -X.X% with color span."""
    if signed:
        sign = "+" if val >= 0 else ""
        color = _CLR_GREEN if val >= 0 else _CLR_RED
        return f'<span style="color:{color}">{sign}{val:.1f}%</span>'
    return f"{val:.1f}%"


def _fmt_pct_plain(val: float) -> str:
    """Format as X.X% without color."""
    return f"{val:.1f}%"


def _deviation_style(deviation: float) -> str:
    if deviation > 0:
        return f"color: {_CLR_GREEN}"
    elif deviation < 0:
        return f"color: {_CLR_RED}"
    return ""


# ── Inline style building blocks ─────────────────────────────────────────────


def _section_header(title: str) -> str:
    """Render a section header card-style."""
    return (
        f'<div style="background:{_CLR_SECTION};color:#fff;padding:10px 16px;'
        f"border-radius:6px 6px 0 0;margin-top:24px;"
        f'font-size:16px;font-weight:bold">'
        f"{escape(title)}</div>"
    )


def _card_open() -> str:
    return '<div style="border:1px solid #e0e0e0;border-radius:0 0 6px 6px;padding:12px 16px;margin-bottom:8px">'


def _card_close() -> str:
    return "</div>"


def _table_open(headers: list[str], col_aligns: list[str] | None = None) -> str:
    """Start a table with header row. col_aligns: 'l' or 'r' per column."""
    aligns = col_aligns or ["l"] * len(headers)
    parts = [
        '<table style="border-collapse:collapse;width:100%;font-size:14px;margin:8px 0">',
        "<thead><tr>",
    ]
    for h, a in zip(headers, aligns, strict=True):
        align = "right" if a == "r" else "left"
        parts.append(
            f'<th style="padding:6px 10px;text-align:{align};border-bottom:2px solid #333;font-size:13px">{h}</th>'
        )
    parts.append("</tr></thead><tbody>")
    return "\n".join(parts)


def _table_close() -> str:
    return "</tbody></table>"


def _table_row(cells: list[str], aligns: list[str], row_idx: int, bold: bool = False) -> str:
    """Render a table row with alternating background."""
    bg = _CLR_ROW_ALT if row_idx % 2 == 0 else _CLR_ROW_EVEN
    weight = "font-weight:bold;" if bold else ""
    border = "border-top:2px solid #333;border-bottom:2px solid #333;" if bold else ""
    parts = [f'<tr style="background:{bg};{border}">']
    for c, a in zip(cells, aligns, strict=True):
        align = "right" if a == "r" else "left"
        parts.append(
            f'<td style="padding:5px 10px;text-align:{align};border-bottom:1px solid #e5e5e5;{weight}">{c}</td>'
        )
    parts.append("</tr>")
    return "".join(parts)


# ── Section renderers ─────────────────────────────────────────────────────────


def _render_alerts(alerts: list[str]) -> str:
    """Render alert banner at top."""
    if not alerts:
        return ""
    items = "".join(f'<li style="margin:4px 0">{escape(a)}</li>' for a in alerts)
    return (
        f'<div style="background:{_CLR_RED};color:#fff;padding:12px 16px;'
        f'border-radius:6px;margin-bottom:16px">'
        f'<strong style="font-size:15px">Alerts</strong>'
        f'<ul style="margin:8px 0 0 0;padding-left:20px">{items}</ul>'
        f"</div>"
    )


def _render_narrative(narrative: str) -> str:
    """Render AI narrative as styled blockquote."""
    return (
        f"{_section_header('Market Narrative')}"
        f"{_card_open()}"
        f'<blockquote style="margin:0;padding:8px 16px;border-left:4px solid {_CLR_ACCENT};'
        f'color:#444;font-style:italic">'
        f"{escape(narrative)}"
        f"</blockquote>"
        f"{_card_close()}"
    )


def _render_activity(activity: ActivityData) -> str:
    """Render investment activity section (aggregated by symbol)."""
    parts = [_section_header("Investment Activity"), _card_open()]
    parts.append(
        f'<p style="margin:4px 0;color:#666;font-size:13px">'
        f"Period: {escape(activity.period_start)} — {escape(activity.period_end)}</p>"
    )

    # Summary first (most important info at the top)
    parts.append(_table_open(["", "Amount"], ["l", "r"]))
    summary_rows: list[tuple[str, float]] = [
        (f"Deposits ({len(activity.deposits)}x)", activity.net_cash_in),
        (
            f"Buys ({len(activity.buys)}x)",
            -activity.net_deployed if activity.net_deployed > 0 else activity.net_deployed,
        ),
        (f"Sells ({len(activity.sells)}x)", sum(s["amount"] for s in activity.sells)),
        (f"Dividends ({len(activity.dividends)}x)", sum(d["amount"] for d in activity.dividends)),
    ]
    if activity.interest_total:
        summary_rows.append(("Interest", activity.interest_total))
    if activity.foreign_tax_total:
        summary_rows.append(("Foreign Tax", activity.foreign_tax_total))
    for i, (label, val) in enumerate(summary_rows):
        color = f' style="color:{_CLR_GREEN}"' if val > 0 else ""
        parts.append(_table_row([label, f"<span{color}>{_fmt_currency(val)}</span>"], ["l", "r"], i))
    parts.append(_table_close())

    max_rows = ACTIVITY_TOP_SYMBOLS

    def _render_agg_table(label: str, col2_name: str, agg: list[tuple[str, int, float]]) -> None:
        """Render aggregated ticker table with expandable overflow."""
        parts.append(f'<p style="margin:12px 0 4px;font-weight:bold;font-size:13px">{label}</p>')
        parts.append(_table_open(["Symbol", col2_name, "Total"], ["l", "r", "r"]))
        for idx, (sym, cnt, tot) in enumerate(agg[:max_rows]):
            parts.append(_table_row([escape(sym), str(cnt), _fmt_currency(tot)], ["l", "r", "r"], idx))
        if len(agg) > max_rows:
            rest = agg[max_rows:]
            rest_total = sum(t for _, _, t in rest)
            parts.append(
                f"<tr><td colspan='3' style='padding:4px 10px;color:#999;font-size:0.85rem'>"
                f"... and {len(rest)} more ({_fmt_currency(rest_total)})</td></tr>"
            )
        parts.append(_table_close())

    # Buys aggregated by symbol (top 5 + expandable others)
    if activity.buys_by_symbol:
        _render_agg_table("Buys by Ticker", "Trades", activity.buys_by_symbol)

    # Dividends aggregated by symbol (top 5 + expandable others)
    if activity.dividends_by_symbol:
        _render_agg_table("Dividends by Ticker", "Payments", activity.dividends_by_symbol)

    parts.append(_card_close())
    return "\n".join(parts)


def _render_balance_sheet(bs: BalanceSheetData) -> str:
    """Render balance sheet section."""
    parts = [_section_header("Balance Sheet"), _card_open()]

    def _fmt_acct(acct: AccountBalance) -> str:
        if acct.currency == "CNY":
            return f"&yen;{acct.balance:,.2f}"
        return _fmt_currency(acct.balance)

    # Assets
    parts.append('<p style="margin:8px 0 4px;font-weight:bold;font-size:14px">Assets</p>')
    parts.append(_table_open(["Account", "Balance"], ["l", "r"]))
    row_idx = 0
    parts.append(_table_row(["Fidelity (all accounts)", _fmt_currency(bs.investment_total)], ["l", "r"], row_idx))
    row_idx += 1
    # USD accounts (skip zero)
    for acct in bs.accounts:
        if acct.currency != "CNY" and abs(acct.balance) >= 0.01:
            parts.append(_table_row([escape(acct.name), _fmt_acct(acct)], ["l", "r"], row_idx))
            row_idx += 1
    # CNY accounts
    cny_accts = [a for a in bs.accounts if a.currency == "CNY" and abs(a.balance) >= 0.01]
    if cny_accts:
        cny_total = sum(a.balance for a in cny_accts)
        parts.append(
            _table_row([f'<em style="color:#666">CNY assets (&yen;{cny_total:,.0f})</em>', ""], ["l", "r"], row_idx)
        )
        row_idx += 1
        for acct in cny_accts:
            parts.append(_table_row([f"&nbsp;&nbsp;{escape(acct.name)}", _fmt_acct(acct)], ["l", "r"], row_idx))
            row_idx += 1
    parts.append(
        _table_row(
            ["<strong>Total Assets</strong>", f"<strong>{_fmt_currency(bs.total_assets)}</strong>"],
            ["l", "r"],
            row_idx,
            bold=True,
        )
    )
    parts.append(_table_close())

    # Liabilities
    if bs.credit_cards:
        parts.append('<p style="margin:12px 0 4px;font-weight:bold;font-size:14px">Liabilities</p>')
        parts.append(_table_open(["Account", "Balance"], ["l", "r"]))
        for i, cc in enumerate(bs.credit_cards):
            parts.append(_table_row([escape(cc.name), _fmt_currency(cc.balance)], ["l", "r"], i))
        parts.append(
            _table_row(
                ["<strong>Total Liabilities</strong>", f"<strong>{_fmt_currency(bs.total_liabilities)}</strong>"],
                ["l", "r"],
                len(bs.credit_cards),
                bold=True,
            )
        )
        parts.append(_table_close())

    # Net Worth
    color = _CLR_GREEN if bs.net_worth >= 0 else _CLR_RED
    parts.append(
        f'<div style="margin-top:12px;padding:10px;background:{_CLR_ROW_ALT};'
        f'border-radius:4px;text-align:center">'
        f'<span style="font-size:14px;color:#666">Net Worth</span><br>'
        f'<span style="font-size:22px;font-weight:bold;color:{color}">'
        f"{_fmt_currency(bs.net_worth)}</span></div>"
    )

    parts.append(_card_close())
    return "\n".join(parts)


def _render_cashflow(cf: CashFlowData) -> str:
    """Render cash flow section."""
    parts = [_section_header(f"Cash Flow — {cf.period}"), _card_open()]

    # Income table
    parts.append('<p style="margin:8px 0 4px;font-weight:bold;font-size:14px">Income</p>')
    parts.append(_table_open(["Category", "Count", "Amount"], ["l", "r", "r"]))
    for i, item in enumerate(cf.income_items):
        parts.append(
            _table_row(
                [escape(item.category), str(item.count), _fmt_currency(item.amount)],
                ["l", "r", "r"],
                i,
            )
        )
    parts.append(
        _table_row(
            ["<strong>Total Income</strong>", "", f"<strong>{_fmt_currency(cf.total_income)}</strong>"],
            ["l", "r", "r"],
            len(cf.income_items),
            bold=True,
        )
    )
    parts.append(_table_close())

    # Expense table
    parts.append('<p style="margin:12px 0 4px;font-weight:bold;font-size:14px">Expenses</p>')
    parts.append(_table_open(["Category", "Count", "Amount"], ["l", "r", "r"]))
    major_exp = [item for item in cf.expense_items if item.amount >= MAJOR_EXPENSE_THRESHOLD]
    minor_exp = [item for item in cf.expense_items if item.amount < MAJOR_EXPENSE_THRESHOLD]
    for i, item in enumerate(major_exp):
        parts.append(
            _table_row([escape(item.category), str(item.count), _fmt_currency(item.amount)], ["l", "r", "r"], i)
        )
    if minor_exp:
        minor_total = sum(item.amount for item in minor_exp)
        parts.append(
            f"<tr><td colspan='3' style='padding:4px 10px;color:#999;font-size:0.85rem'>"
            f"... and {len(minor_exp)} more ({_fmt_currency(minor_total)})</td></tr>"
        )
    parts.append(
        _table_row(
            ["<strong>Total Expenses</strong>", "", f"<strong>{_fmt_currency(cf.total_expenses)}</strong>"],
            ["l", "r", "r"],
            len(major_exp) + (1 if minor_exp else 0),
            bold=True,
        )
    )
    parts.append(_table_close())

    # Summary
    parts.append('<p style="margin:12px 0 4px;font-weight:bold;font-size:14px">Summary</p>')
    parts.append(_table_open(["", "Amount"], ["l", "r"]))
    sr_color = _CLR_GREEN if cf.savings_rate >= 0 else _CLR_RED
    summary_items = [
        ("Net Cash Flow", f'<span style="color:{_CLR_GREEN}">{_fmt_currency(cf.net_cashflow)}</span>'),
        ("Invested", _fmt_currency(cf.invested)),
        ("Credit Card Payments", _fmt_currency(cf.credit_card_payments)),
        ("Gross Savings Rate", f'<span style="color:{sr_color}">{cf.savings_rate:.1f}%</span>'),
        (
            "Take-home Savings Rate",
            f'<span style="color:{_CLR_GREEN if cf.takehome_savings_rate >= 0 else _CLR_RED}">'
            f"{cf.takehome_savings_rate:.1f}%</span>",
        ),
    ]
    for i, (label, val) in enumerate(summary_items):
        parts.append(_table_row([label, val], ["l", "r"], i))
    parts.append(_table_close())

    parts.append(_card_close())
    return "\n".join(parts)


def _render_market(market: MarketData) -> str:
    """Render market context section."""
    parts = [_section_header("Market Context"), _card_open()]

    # Index returns
    if market.indices:
        parts.append('<p style="margin:8px 0 4px;font-weight:bold;font-size:14px">Index Returns</p>')
        parts.append(_table_open(["Index", "Current", "Month", "YTD"], ["l", "r", "r", "r"]))
        for i, idx in enumerate(market.indices):
            parts.append(
                _table_row(
                    [
                        escape(idx.name),
                        f"{idx.current:,.1f}",
                        _fmt_pct(idx.month_return),
                        _fmt_pct(idx.ytd_return),
                    ],
                    ["l", "r", "r", "r"],
                    i,
                )
            )
        if market.portfolio_month_return is not None:
            parts.append(
                _table_row(
                    ["<strong>Portfolio</strong>", "", _fmt_pct(market.portfolio_month_return), ""],
                    ["l", "r", "r", "r"],
                    len(market.indices),
                    bold=True,
                )
            )
        parts.append(_table_close())

    # Macro indicators
    indicators: list[tuple[str, str]] = []
    if market.fed_rate is not None:
        indicators.append(("Fed Rate", f"{market.fed_rate:.2f}%"))
    if market.treasury_10y is not None:
        indicators.append(("10Y Treasury", f"{market.treasury_10y:.2f}%"))
    if market.cpi is not None:
        indicators.append(("CPI", f"{market.cpi:.1f}%"))
    if market.unemployment is not None:
        indicators.append(("Unemployment", f"{market.unemployment:.1f}%"))
    if market.vix is not None:
        indicators.append(("VIX", f"{market.vix:.1f}"))
    if market.dxy is not None:
        indicators.append(("DXY", f"{market.dxy:.1f}"))
    if market.usd_cny is not None:
        indicators.append(("USD/CNY", f"{market.usd_cny:.2f}"))
    if market.gold_return is not None:
        indicators.append(("Gold (Month)", _fmt_pct(market.gold_return)))
    if market.btc_return is not None:
        indicators.append(("BTC (Month)", _fmt_pct(market.btc_return)))

    if indicators:
        parts.append('<p style="margin:12px 0 4px;font-weight:bold;font-size:14px">Macro Indicators</p>')
        # Render as 2-column grid
        parts.append('<table style="border-collapse:collapse;width:100%;font-size:14px;margin:8px 0">')
        for i in range(0, len(indicators), 2):
            bg = _CLR_ROW_ALT if (i // 2) % 2 == 0 else _CLR_ROW_EVEN
            left = indicators[i]
            right = indicators[i + 1] if i + 1 < len(indicators) else ("", "")
            parts.append(
                f'<tr style="background:{bg}">'
                f'<td style="padding:5px 10px;border-bottom:1px solid #e5e5e5;color:#666;width:25%">{left[0]}</td>'
                f'<td style="padding:5px 10px;border-bottom:1px solid #e5e5e5;text-align:right;width:25%">{left[1]}</td>'
                f'<td style="padding:5px 10px;border-bottom:1px solid #e5e5e5;color:#666;width:25%">{right[0]}</td>'
                f'<td style="padding:5px 10px;border-bottom:1px solid #e5e5e5;text-align:right;width:25%">{right[1]}</td>'
                f"</tr>"
            )
        parts.append("</table>")

    parts.append(_card_close())
    return "\n".join(parts)


def _render_holdings_detail(detail: HoldingsDetailData) -> str:
    """Render holdings detail section with top/bottom performers and earnings."""
    parts = [_section_header("Holdings Detail"), _card_open()]

    # Top performers
    if detail.top_performers:
        parts.append('<p style="margin:8px 0 4px;font-weight:bold;font-size:14px">Top Performers</p>')
        parts.append(_table_open(["Ticker", "Month Return", "Start", "End", "Earnings"], ["l", "r", "r", "r", "l"]))
        for i, s in enumerate(detail.top_performers):
            parts.append(
                _table_row(
                    [
                        f"<strong>{escape(s.ticker)}</strong>",
                        _fmt_pct(s.month_return),
                        _fmt_currency(s.start_value),
                        _fmt_currency(s.end_value),
                        escape(s.next_earnings or ""),
                    ],
                    ["l", "r", "r", "r", "l"],
                    i,
                )
            )
        parts.append(_table_close())

    # Bottom performers
    if detail.bottom_performers:
        parts.append('<p style="margin:12px 0 4px;font-weight:bold;font-size:14px">Bottom Performers</p>')
        parts.append(_table_open(["Ticker", "Month Return", "Start", "End", "Earnings"], ["l", "r", "r", "r", "l"]))
        for i, s in enumerate(detail.bottom_performers):
            parts.append(
                _table_row(
                    [
                        f"<strong>{escape(s.ticker)}</strong>",
                        _fmt_pct(s.month_return),
                        _fmt_currency(s.start_value),
                        _fmt_currency(s.end_value),
                        escape(s.next_earnings or ""),
                    ],
                    ["l", "r", "r", "r", "l"],
                    i,
                )
            )
        parts.append(_table_close())

    # Upcoming earnings
    if detail.upcoming_earnings:
        parts.append('<p style="margin:12px 0 4px;font-weight:bold;font-size:14px">Upcoming Earnings</p>')
        parts.append(_table_open(["Ticker", "Date", "Month Return"], ["l", "l", "r"]))
        for i, s in enumerate(detail.upcoming_earnings):
            parts.append(
                _table_row(
                    [
                        f"<strong>{escape(s.ticker)}</strong>",
                        escape(s.next_earnings or ""),
                        _fmt_pct(s.month_return),
                    ],
                    ["l", "l", "r"],
                    i,
                )
            )
        parts.append(_table_close())

    parts.append(_card_close())
    return "\n".join(parts)


def _render_cross_reconciliation(xr: CrossReconciliationData) -> str:
    """Render cross reconciliation section."""
    parts = [_section_header("Cross Reconciliation"), _card_open()]

    # Summary line
    parts.append(
        f'<p style="margin:4px 0;font-size:13px;color:#666">'
        f"Qianji Total: {_fmt_currency(xr.qianji_total)} &bull; "
        f"Fidelity Total: {_fmt_currency(xr.fidelity_total)} &bull; "
        f"Unmatched: {_fmt_currency(xr.unmatched_amount)}</p>"
    )

    # Matched pairs
    if xr.matched:
        parts.append(
            f'<p style="margin:12px 0 4px;font-weight:bold;font-size:14px">Matched Transfers ({len(xr.matched)})</p>'
        )
        parts.append(_table_open(["Qianji Date", "Fidelity Date", "Amount"], ["l", "l", "r"]))
        for i, m in enumerate(xr.matched):
            # Note date differences
            date_note = ""
            if m.date_qianji != m.date_fidelity:
                date_note = ' <span style="color:#999;font-size:11px">(&plusmn;1d)</span>'
            parts.append(
                _table_row(
                    [escape(m.date_qianji), escape(m.date_fidelity) + date_note, _fmt_currency(m.amount)],
                    ["l", "l", "r"],
                    i,
                )
            )
        parts.append(_table_close())

    # Unmatched Qianji
    if xr.unmatched_qianji:
        parts.append('<p style="margin:12px 0 4px;font-weight:bold;font-size:14px">Unmatched Qianji</p>')
        parts.append(_table_open(["Date", "Amount", "Note"], ["l", "r", "l"]))
        for i, u in enumerate(xr.unmatched_qianji):
            parts.append(
                _table_row(
                    [escape(str(u.get("date", ""))), _fmt_currency(u["amount"]), escape(str(u.get("note", "")))],
                    ["l", "r", "l"],
                    i,
                )
            )
        parts.append(_table_close())

    # Unmatched Fidelity
    if xr.unmatched_fidelity:
        parts.append('<p style="margin:12px 0 4px;font-weight:bold;font-size:14px">Unmatched Fidelity</p>')
        parts.append(_table_open(["Date", "Amount"], ["l", "r"]))
        for i, u in enumerate(xr.unmatched_fidelity):
            parts.append(_table_row([escape(str(u.get("date", ""))), _fmt_currency(u["amount"])], ["l", "r"], i))
        parts.append(_table_close())

    parts.append(_card_close())
    return "\n".join(parts)


# ── Core section renderers (preserved from original) ──────────────────────────


def _holdings_table(report: ReportData) -> str:
    """Build the holdings overview table."""
    rows: list[str] = []

    def _h_row(name: str, lots: int, value: float, pct: float, *, cls: str = "", indent: int = 0) -> str:
        pad = "&nbsp;&nbsp;" * indent
        return (
            f"<tr{' class="' + cls + '"' if cls else ''}>"
            f"<td>{pad}{name}</td>"
            f"<td class='num'>{lots}</td>"
            f"<td class='num'>${value:,.2f}</td>"
            f"<td class='num'>{pct:.2f}%</td></tr>"
        )

    def _render_holdings(holdings: list[HoldingData], indent: int) -> None:
        major = [h for h in holdings if h.pct >= MIN_HOLDING_PCT]
        minor = [h for h in holdings if h.pct < MIN_HOLDING_PCT]
        for h in major:
            rows.append(_h_row(escape(h.ticker), h.lots, h.value, h.pct, indent=indent))
        if minor:
            minor_val = sum(h.value for h in minor)
            minor_lots = sum(h.lots for h in minor)
            minor_pct = sum(h.pct for h in minor)
            pad = "&nbsp;&nbsp;" * indent
            names = ", ".join(h.ticker for h in minor)
            rows.append(_h_row(f"{pad}Others ({names})", minor_lots, minor_val, minor_pct))

    rows.append(_h_row("<strong>PORTFOLIO</strong>", report.total_lots, report.total, 100.0, cls="total"))

    for cat in report.equity_categories:
        rows.append(_h_row(f"<strong>{escape(cat.name)}</strong>", cat.lots, cat.value, cat.pct, cls="cat"))
        for grp in cat.subtypes:
            rows.append(
                _h_row(
                    f"<em>{escape(grp.name.capitalize())}</em>", grp.lots, grp.value, grp.pct, cls="subtype", indent=1
                )
            )
            _render_holdings(grp.holdings, indent=2)

    if report.non_equity_categories:
        non_eq_value = sum(c.value for c in report.non_equity_categories)
        non_eq_lots = sum(c.lots for c in report.non_equity_categories)
        non_eq_pct = sum(c.pct for c in report.non_equity_categories)
        rows.append(_h_row("<strong>Non-Equity</strong>", non_eq_lots, non_eq_value, non_eq_pct, cls="cat"))
        for cat in report.non_equity_categories:
            rows.append(
                _h_row(f"<strong>{escape(cat.name)}</strong>", cat.lots, cat.value, cat.pct, cls="subtype", indent=1)
            )
            _render_holdings(cat.holdings, indent=2)

    return "\n".join(rows)


def _summary_table(report: ReportData) -> str:
    """Build the category summary table."""
    rows: list[str] = []

    def _cat_row(cat: CategoryData, indent: bool = False) -> str:
        style = _deviation_style(cat.deviation)
        name = f"&nbsp;&nbsp;{escape(cat.name)}" if indent else f"<strong>{escape(cat.name)}</strong>"
        dev = f"{cat.deviation:+.1f}%" if cat.target else ""
        target = f"{cat.target}%" if cat.target else ""
        return (
            f"<tr><td>{name}</td>"
            f"<td class='num'>${cat.value:,.2f}</td>"
            f"<td class='num'>{cat.pct:.2f}%</td>"
            f"<td class='num'>{target}</td>"
            f"<td class='num' style='{style}'>{dev}</td></tr>"
        )

    for cat in report.equity_categories:
        rows.append(_cat_row(cat))
        for grp in cat.subtypes:
            rows.append(
                f"<tr><td>&nbsp;&nbsp;<em>{escape(grp.name.capitalize())}</em></td>"
                f"<td class='num'>${grp.value:,.2f}</td>"
                f"<td class='num'>{grp.pct:.2f}%</td>"
                f"<td></td><td></td></tr>"
            )

    if report.non_equity_categories:
        non_eq_value = sum(c.value for c in report.non_equity_categories)
        non_eq_pct = sum(c.pct for c in report.non_equity_categories)
        non_eq_target = sum(c.target for c in report.non_equity_categories)
        non_eq_dev = non_eq_pct - non_eq_target
        style = _deviation_style(non_eq_dev)
        rows.append(
            f"<tr><td><strong>Non-Equity</strong></td>"
            f"<td class='num'>${non_eq_value:,.2f}</td>"
            f"<td class='num'>{non_eq_pct:.2f}%</td>"
            f"<td class='num'>{non_eq_target}%</td>"
            f"<td class='num' style='{style}'>{non_eq_dev:+.1f}%</td></tr>"
        )
        for cat in report.non_equity_categories:
            rows.append(_cat_row(cat, indent=True))

    weight_total = sum(c.target for c in report.equity_categories) + sum(c.target for c in report.non_equity_categories)
    rows.append(
        f"<tr class='total'><td><strong>TOTAL</strong></td>"
        f"<td class='num'>${report.total:,.2f}</td>"
        f"<td class='num'>100.00%</td>"
        f"<td class='num'>{weight_total}%</td>"
        f"<td></td></tr>"
    )

    return "\n".join(rows)


def _contribution_table(contrib: ContributionData) -> str:
    """Build the contribution guide table."""
    rows: list[str] = []
    for row in contrib.rows:
        arrow = " &rarr;" if row.improving else ""
        rows.append(
            f"<tr><td>{escape(row.category)}</td>"
            f"<td class='num'>${row.allocate:,.0f}</td>"
            f"<td class='num'>${row.new_value:,.0f}</td>"
            f"<td class='num'>{row.new_pct:.1f}%</td>"
            f"<td class='num'>{arrow} {row.target}%</td></tr>"
        )
    rows.append(
        f"<tr class='total'><td><strong>TOTAL</strong></td>"
        f"<td class='num'>${contrib.amount:,.0f}</td>"
        f"<td class='num'>${contrib.new_total:,.0f}</td>"
        f"<td></td><td></td></tr>"
    )
    return "\n".join(rows)


# ── Main render function ─────────────────────────────────────────────────────


def render(report: ReportData, *, email_safe: bool = False) -> str:
    """Render the full report as a self-contained HTML string.

    When email_safe=True, SVG charts are omitted (Gmail strips them).

    Section order (inverted pyramid — most actionable first):
    1. Alerts / Narrative (if any)
    2. Category Summary + Goal Progress
    3. Contribution Guide (if applicable)
    4. Cash Flow (monthly)
    5. Investment Activity (monthly)
    6. Balance Sheet
    7. Holdings Detail (full positions)
    8. Market Context / Holdings Detail (if available)
    9. Cross Reconciliation (audit)
    """
    sections: list[str] = []

    # Alerts & Narrative
    if report.alerts:
        sections.append(_render_alerts(report.alerts))
    if report.narrative is not None:
        sections.append(_render_narrative(report.narrative))

    # Title
    sections.append(
        f'<h1 style="border-bottom:2px solid #333;padding-bottom:0.5rem">'
        f"Portfolio Snapshot — {escape(report.date)}</h1>"
    )

    # Headline metrics
    goal_html = ""
    if report.goal > 0:
        goal_html = (
            f'<p style="margin:8px 0;font-size:14px"><strong>Progress:</strong> '
            f"{report.goal_pct:.2f}% of ${report.goal:,.0f} goal</p>"
        )
    metrics: list[str] = []
    _m_style = "flex:1;text-align:center;padding:12px 8px;background:#f8f9fa;border-radius:8px;min-width:120px"
    metrics.append(
        f'<div style="{_m_style}">'
        f'<div style="font-size:12px;color:#666">Portfolio</div>'
        f'<div style="font-size:22px;font-weight:bold">{_fmt_currency(report.total)}</div></div>'
    )
    if report.balance_sheet:
        metrics.append(
            f'<div style="{_m_style}">'
            f'<div style="font-size:12px;color:#666">Net Worth</div>'
            f'<div style="font-size:22px;font-weight:bold">'
            f"{_fmt_currency(report.balance_sheet.net_worth)}</div></div>"
        )
    if report.cashflow:
        sr = report.cashflow.savings_rate
        sr_color = _CLR_GREEN if sr >= 0 else _CLR_RED
        metrics.append(
            f'<div style="{_m_style}">'
            f'<div style="font-size:12px;color:#666">Savings Rate</div>'
            f'<div style="font-size:22px;font-weight:bold;color:{sr_color}">{sr:.0f}%</div></div>'
        )
    if report.goal > 0:
        metrics.append(
            f'<div style="{_m_style}">'
            f'<div style="font-size:12px;color:#666">Goal</div>'
            f'<div style="font-size:22px;font-weight:bold">{report.goal_pct:.0f}%</div></div>'
        )
    sections.append(f'<div style="display:flex;gap:12px;margin:12px 0;flex-wrap:wrap">{"".join(metrics)}</div>')

    # Portfolio Trend chart (SVG — skip in email)
    if not email_safe and report.chart_data and report.chart_data.net_worth_trend:
        trend_svg = _svg.trend_line(report.chart_data.net_worth_trend)
        if trend_svg:
            sections.append(
                f"{_section_header('Portfolio Trend')}"
                f'{_card_open()}<div style="text-align:center">{trend_svg}</div>{_card_close()}'
            )

    # Category Summary + Allocation Donut
    # Category Summary table + optional donut chart (SVG — skip in email)
    all_cats = report.equity_categories + report.non_equity_categories
    summary_table = (
        f"{_table_open(['Category', 'Value', 'Actual', 'Target', 'Deviation'], ['l', 'r', 'r', 'r', 'r'])}"
        f"{_summary_table(report)}{_table_close()}{goal_html}"
    )
    if not email_safe:
        donut_svg = _svg.allocation_donut(all_cats)
        donut_html = (
            f'<div style="display:flex;flex-wrap:wrap;align-items:flex-start;gap:24px">'
            f'<div style="flex:1;min-width:300px">{summary_table}</div>'
            f'<div style="flex:0 0 auto">{donut_svg}</div></div>'
            if donut_svg
            else summary_table
        )
    else:
        donut_html = summary_table
    summary_header = _section_header("Category Summary")
    sections.append(f"{summary_header}\n{_card_open()}\n{donut_html}\n{_card_close()}")

    # Contribution Guide
    if report.contribution:
        sections.append(f"""{_section_header(f"Next Contribution Guide (${report.contribution.amount:,.0f})")}
{_card_open()}
{_table_open(["Category", "Allocate", "New Value", "New %", "Target"], ["l", "r", "r", "r", "r"])}
{_contribution_table(report.contribution)}
{_table_close()}
{_card_close()}""")

    # Cash Flow
    if report.cashflow is not None:
        sections.append(_render_cashflow(report.cashflow))

    # Income vs Expenses chart (SVG — skip in email)
    if not email_safe and report.chart_data and report.chart_data.monthly_flows:
        bars_svg = _svg.monthly_bars(report.chart_data.monthly_flows)
        if bars_svg:
            sections.append(
                f"{_section_header('Income vs Expenses')}"
                f'{_card_open()}<div style="text-align:center">{bars_svg}</div>{_card_close()}'
            )

    # Investment Activity
    if report.activity is not None:
        sections.append(_render_activity(report.activity))

    # Balance Sheet
    if report.balance_sheet is not None:
        sections.append(_render_balance_sheet(report.balance_sheet))

    # Holdings Detail (full positions table)
    sections.append(f"""{_section_header("Holdings Detail")}
{_card_open()}
{_table_open(["Ticker", "Lots", "Value", "%"], ["l", "r", "r", "r"])}
{_holdings_table(report)}
{_table_close()}
{_card_close()}""")

    # Market Context
    if report.market is not None:
        sections.append(_render_market(report.market))

    # Holdings Detail (per-stock deep dive)
    if report.holdings_detail is not None:
        sections.append(_render_holdings_detail(report.holdings_detail))

    # Cross Reconciliation
    if report.cross_reconciliation is not None:
        sections.append(_render_cross_reconciliation(report.cross_reconciliation))

    body = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portfolio Snapshot — {escape(report.date)}</title>
<style>
  body {{ font-family: {_FONT_STACK};
         max-width: 640px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
  h1 {{ border-bottom: 2px solid #333; padding-bottom: 0.5rem; }}
  h2 {{ margin-top: 2rem; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: 0.9rem; }}
  th, td {{ padding: 4px 10px; text-align: left; border-bottom: 1px solid #e5e5e5; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  tr.total {{ font-weight: bold; border-top: 2px solid #333; border-bottom: 2px solid #333; }}
  tr.cat {{ background: #f9fafb; }}
  tr.subtype {{ background: #f3f4f6; }}
</style>
</head>
<body>
{body}
</body>
</html>"""
