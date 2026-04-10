"""Post-build validation gate. Blocks sync on FATAL checks."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .db import get_connection

log = logging.getLogger(__name__)


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
    """Flag consecutive days where total changes by more than 10%."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT date, total FROM computed_daily ORDER BY date",
        ).fetchall()
    finally:
        conn.close()

    results: list[CheckResult] = []
    for i in range(1, len(rows)):
        prev_date, prev_total = rows[i - 1]
        curr_date, curr_total = rows[i]
        if prev_total == 0:
            continue
        pct_change = abs((curr_total - prev_total) / prev_total) * 100
        if pct_change > 10:
            results.append(CheckResult(
                name="day_over_day",
                severity=Severity.FATAL,
                message=f"{prev_date} -> {curr_date}: {pct_change:.1f}% change ({prev_total:,.0f} -> {curr_total:,.0f})",
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
    results.extend(_check_date_gaps(db_path))
    return results
