"""Post-build validation gate. Blocks sync on FATAL checks."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from pathlib import Path

from .db import get_connection

log = logging.getLogger(__name__)

# Day-over-day anomalies older than this (relative to the latest computed_daily
# date) are not actionable: `daily_close` is immutable past the refresh window
# (PR #98), so an old jump is a permanent fact (typically a 401k QFX-snapshot
# step-function). Only surface recent spikes where the user can actually
# investigate an incoming data issue.
_DAY_OVER_DAY_WINDOW_DAYS = 7

# Known valid categories in ``computed_daily_tickers``. Updated when a new
# asset class lands; unknown values surface as a validation FATAL so a typo
# in the allocation classifier is caught at build time.
_KNOWN_CATEGORIES: frozenset[str] = frozenset({
    "US Equity", "Non-US Equity", "Crypto", "Safe Net", "Liability",
})

# Known valid subtypes (empty string = unclassified, e.g. Crypto / Safe Net /
# Liability). Unknown values surface as FATAL.
_KNOWN_SUBTYPES: frozenset[str] = frozenset({"", "broad", "growth"})

# Tickers valued from Qianji balances, Fidelity cash, or face value — no
# `daily_close` row by design, so price-freshness checks skip them.
_NON_PRICE_TICKERS = frozenset({
    "SPAXX", "FZFXX", "FDRXX",  # Fidelity money market (cash sweep)
    "Debit Cash", "I Bonds", "CNY Assets", "Gift Card", "Cash",  # Qianji book-value
    "Amex HYSA", "Amex Saving", "USDC", "T-Bills",  # Qianji + CUSIPs
    "Robinhood",  # Qianji book-value
    "401k sp500", "401k ex-us", "401k tech",  # Empower proxy
    "Alipay Funds", "Managed Fund", "蓝天宇代管",  # CNY assets
})


# ── Types ───────────────────────────────────────────────────────────────────


class Severity(Enum):
    FATAL = "FATAL"
    WARNING = "WARNING"


@dataclass
class CheckResult:
    name: str
    severity: Severity
    message: str


# ── Individual checks ───────────────────────────────────────────────────────


def _check_total_vs_tickers(db_path: Path) -> list[CheckResult]:
    """Verify computed_daily.total matches SUM(computed_daily_tickers.value) per date."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT cd.date, cd.total, COALESCE(t.ticker_sum, 0) AS ticker_sum
            FROM computed_daily cd
            LEFT JOIN (
                SELECT date, SUM(value) AS ticker_sum
                FROM computed_daily_tickers
                WHERE value > 0
                GROUP BY date
            ) t ON cd.date = t.date
            """,
        ).fetchall()
    finally:
        conn.close()

    results: list[CheckResult] = []
    for dt, total, ticker_sum in rows:
        diff = abs(total - ticker_sum)
        if diff > 1.0:
            results.append(CheckResult(
                name="total_vs_tickers",
                severity=Severity.FATAL,
                message=f"{dt}: total={total:,.2f} vs tickers={ticker_sum:,.2f} (diff={diff:,.2f})",
            ))
    return results


def _check_day_over_day(db_path: Path) -> list[CheckResult]:
    """Flag suspicious day-over-day total changes within the recent window
    (anchored to the latest computed_daily date, not wall-clock today)."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT date, total FROM computed_daily ORDER BY date",
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []
    latest_date = date.fromisoformat(rows[-1][0])
    cutoff_date = latest_date - timedelta(days=_DAY_OVER_DAY_WINDOW_DAYS)

    results: list[CheckResult] = []
    for i in range(1, len(rows)):
        prev_date, prev_total = rows[i - 1]
        curr_date, curr_total = rows[i]
        if prev_total == 0:
            continue
        if date.fromisoformat(curr_date) < cutoff_date:
            continue
        pct_change = abs((curr_total - prev_total) / prev_total) * 100
        abs_change = abs(curr_total - prev_total)
        if pct_change > 20 and abs_change > 10000:
            results.append(CheckResult(
                name="day_over_day",
                severity=Severity.FATAL,
                message=f"{prev_date} -> {curr_date}: {pct_change:.1f}% change (${abs_change:,.0f}, {prev_total:,.0f} -> {curr_total:,.0f})",
            ))
        elif pct_change > 15 and abs_change > 5000:
            results.append(CheckResult(
                name="day_over_day",
                severity=Severity.WARNING,
                message=f"{prev_date} -> {curr_date}: {pct_change:.1f}% change (${abs_change:,.0f}, {prev_total:,.0f} -> {curr_total:,.0f})",
            ))
    return results


def _check_holdings_have_prices(db_path: Path) -> list[CheckResult]:
    """Every holding > $100 on latest date should have a row in daily_close."""
    conn = get_connection(db_path)
    try:
        latest = conn.execute("SELECT MAX(date) FROM computed_daily_tickers").fetchone()
        if latest is None or latest[0] is None:
            return []
        latest_date: str = latest[0]

        # Get tickers with value > 100 on latest date
        tickers = conn.execute(
            "SELECT ticker FROM computed_daily_tickers WHERE date = ? AND value > 100",
            (latest_date,),
        ).fetchall()

        missing: list[str] = []
        for (ticker,) in tickers:
            if ticker in _NON_PRICE_TICKERS:
                continue
            price_row = conn.execute(
                "SELECT 1 FROM daily_close WHERE symbol = ? LIMIT 1",
                (ticker,),
            ).fetchone()
            if price_row is None:
                missing.append(ticker)
    finally:
        conn.close()

    results: list[CheckResult] = []
    if missing:
        results.append(CheckResult(
            name="holdings_have_prices",
            severity=Severity.FATAL,
            message=f"{len(missing)} holding(s) > $100 without prices: {', '.join(sorted(missing))}",
        ))
    return results


def _check_cny_rate_freshness(db_path: Path) -> list[CheckResult]:
    """Warn if latest CNY=X rate is more than 7 days behind latest computed date."""
    conn = get_connection(db_path)
    try:
        cny_row = conn.execute(
            "SELECT MAX(date) FROM daily_close WHERE symbol = 'CNY=X'",
        ).fetchone()
        daily_row = conn.execute(
            "SELECT MAX(date) FROM computed_daily",
        ).fetchone()
    finally:
        conn.close()

    if cny_row is None or cny_row[0] is None or daily_row is None or daily_row[0] is None:
        return []

    cny_date = cny_row[0]
    daily_date = daily_row[0]

    # Dates are ISO strings (YYYY-MM-DD), comparable lexicographically.
    # Compute calendar day gap by parsing.
    from datetime import date as _date

    cny_d = _date.fromisoformat(cny_date)
    daily_d = _date.fromisoformat(daily_date)
    gap = (daily_d - cny_d).days

    results: list[CheckResult] = []
    if gap > 7:
        results.append(CheckResult(
            name="cny_rate_freshness",
            severity=Severity.WARNING,
            message=f"Latest CNY=X rate is {gap} days old ({cny_date} vs computed {daily_date})",
        ))
    return results


def _check_holdings_prices_are_fresh(db_path: Path) -> list[CheckResult]:
    """Every held symbol's `daily_close` max date must be within 4 days of the latest
    `computed_daily`. Catches the class of bug where the fetch gate silently skipped a
    subset of symbols, leaving forward-fill to paper over multi-day price holes.

    Threshold: 4 calendar days — covers a standard Fri→Mon gap (3 days) plus one
    federal holiday (e.g. Fri holiday + weekend = 3 days, or Mon holiday + weekend = 3
    days); anything beyond that is genuinely stale.
    """
    conn = get_connection(db_path)
    try:
        latest = conn.execute("SELECT MAX(date) FROM computed_daily").fetchone()
        if latest is None or latest[0] is None:
            return []
        latest_iso: str = latest[0]

        # Held tickers on latest date, excluding book-value / proxy tickers that have
        # no corresponding `daily_close` row by design.
        tickers = conn.execute(
            "SELECT ticker FROM computed_daily_tickers WHERE date = ? AND value > 100",
            (latest_iso,),
        ).fetchall()

        stale: list[tuple[str, str]] = []  # (ticker, max_price_date)
        for (ticker,) in tickers:
            if ticker in _NON_PRICE_TICKERS:
                continue
            row = conn.execute(
                "SELECT MAX(date) FROM daily_close WHERE symbol = ?",
                (ticker,),
            ).fetchone()
            if row is None or row[0] is None:
                # No price at all — already covered by _check_holdings_have_prices.
                continue
            stale.append((ticker, row[0]))
    finally:
        conn.close()

    latest_d = date.fromisoformat(latest_iso)
    flagged = [
        (t, px) for t, px in stale
        if (latest_d - date.fromisoformat(px)).days > 4
    ]
    if not flagged:
        return []
    sample = ", ".join(f"{t}@{px}" for t, px in sorted(flagged)[:5])
    more = f" (+{len(flagged) - 5} more)" if len(flagged) > 5 else ""
    return [CheckResult(
        name="holdings_prices_are_fresh",
        severity=Severity.FATAL,
        message=(
            f"{len(flagged)} held symbol(s) have stale prices (>4 days behind "
            f"computed {latest_iso}): {sample}{more}"
        ),
    )]


def _check_cost_basis_nonneg(db_path: Path) -> list[CheckResult]:
    """Cost basis is the $ paid to acquire a position — always non-negative.

    NULL is allowed (legacy rows, or holdings valued by Qianji book value).
    A negative cost_basis would indicate a replay bug (e.g. net sell-shares
    exceeded buy-shares with inverted sign).
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT date, ticker, cost_basis FROM computed_daily_tickers"
            " WHERE cost_basis IS NOT NULL AND cost_basis < 0",
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return []
    sample = ", ".join(f"{t}@{d}={cb:.2f}" for d, t, cb in rows[:5])
    more = f" (+{len(rows) - 5} more)" if len(rows) > 5 else ""
    return [CheckResult(
        name="cost_basis_nonneg",
        severity=Severity.FATAL,
        message=f"{len(rows)} ticker-date(s) with negative cost_basis: {sample}{more}",
    )]


def _check_category_subtype_enums(db_path: Path) -> list[CheckResult]:
    """Every (category, subtype) in computed_daily_tickers must be in the known set.

    Guards against typos in the allocation classifier that would silently put
    holdings in a bucket the frontend doesn't know how to render.
    """
    conn = get_connection(db_path)
    try:
        pairs = conn.execute(
            "SELECT DISTINCT category, COALESCE(subtype, '') FROM computed_daily_tickers",
        ).fetchall()
    finally:
        conn.close()

    results: list[CheckResult] = []
    bad_cats = sorted({cat for cat, _ in pairs if cat not in _KNOWN_CATEGORIES})
    bad_subs = sorted({sub for _, sub in pairs if sub not in _KNOWN_SUBTYPES})
    if bad_cats:
        results.append(CheckResult(
            name="category_enum",
            severity=Severity.FATAL,
            message=f"Unknown category value(s): {bad_cats}. Add to _KNOWN_CATEGORIES or fix classifier.",
        ))
    if bad_subs:
        results.append(CheckResult(
            name="subtype_enum",
            severity=Severity.FATAL,
            message=f"Unknown subtype value(s): {bad_subs}. Add to _KNOWN_SUBTYPES or fix classifier.",
        ))
    return results


def _check_date_gaps(db_path: Path) -> list[CheckResult]:
    """Warn if any gap between consecutive computed_daily dates exceeds 7 calendar days."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT date FROM computed_daily ORDER BY date",
        ).fetchall()
    finally:
        conn.close()

    if len(rows) < 2:
        return []

    from datetime import date as _date

    results: list[CheckResult] = []
    for i in range(1, len(rows)):
        prev = _date.fromisoformat(rows[i - 1][0])
        curr = _date.fromisoformat(rows[i][0])
        gap = (curr - prev).days
        if gap > 7:
            results.append(CheckResult(
                name="date_gaps",
                severity=Severity.WARNING,
                message=f"{gap}-day gap between {prev.isoformat()} and {curr.isoformat()}",
            ))
    return results


# ── Public API ──────────────────────────────────────────────────────────────


def validate_build(db_path: Path) -> list[CheckResult]:
    """Run all post-build validation checks. Returns list of issues found."""
    results: list[CheckResult] = []
    results.extend(_check_total_vs_tickers(db_path))
    results.extend(_check_day_over_day(db_path))
    results.extend(_check_holdings_have_prices(db_path))
    results.extend(_check_cny_rate_freshness(db_path))
    results.extend(_check_holdings_prices_are_fresh(db_path))
    results.extend(_check_cost_basis_nonneg(db_path))
    results.extend(_check_category_subtype_enums(db_path))
    results.extend(_check_date_gaps(db_path))
    return results
