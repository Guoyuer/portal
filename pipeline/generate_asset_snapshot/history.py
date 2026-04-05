"""Historical data aggregation for trend charts.

Scans multiple Fidelity CSV files and Qianji records to produce
time-series data for net worth trends and monthly income/expense charts.
"""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .types import CURRENCY_RE, QJ_EXPENSE, QJ_INCOME, ChartData, Config, MonthlyFlowPoint, QianjiRecord, SnapshotPoint

_DATE_RE = re.compile(r"Portfolio_Positions_([A-Za-z]+-\d+-\d+)")


def load_portfolio_totals(data_dir: Path) -> list[SnapshotPoint]:
    """Scan all Fidelity position CSVs in data_dir and extract (date, total).

    Does NOT require config — just sums the "Current Value" column raw.
    Returns sorted by date ascending.
    """
    points: list[SnapshotPoint] = []

    for csv_path in data_dir.glob("Portfolio_Positions_*.csv"):
        m = _DATE_RE.search(csv_path.name)
        if not m:
            continue
        try:
            date = datetime.strptime(m.group(1), "%b-%d-%Y").strftime("%Y-%m-%d")
        except ValueError:
            continue

        total = _sum_csv_values(csv_path)
        if total > 0:
            points.append(SnapshotPoint(date=date, total=total))

    return sorted(points, key=lambda p: p.date)


def _sum_csv_values(csv_path: Path) -> float:
    """Sum all 'Current Value' cells in a Fidelity positions CSV."""
    try:
        with csv_path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            headers = {h.lower(): h for h in (reader.fieldnames or [])}
            val_h = headers.get("current value")
            if not val_h:
                return 0.0

            sym_h = headers.get("symbol", "")
            desc_h = headers.get("description", "")
            total = 0.0
            for row in reader:
                # Use Symbol or Description — 401k positions have no Symbol
                identifier = (row.get(sym_h, "") or row.get(desc_h, "") or "").strip()
                if not identifier or identifier.lower() == "pending activity":
                    continue
                val = (row.get(val_h) or "").strip()
                if val and val != "--":
                    total += float(CURRENCY_RE.sub("", val))
            return total
    except OSError as e:
        print(f"  [warn] Cannot read {csv_path.name}: {e}", file=sys.stderr)
        return 0.0
    except ValueError as e:
        print(f"  [ERROR] Bad value in {csv_path.name}: {e} — skipping file", file=sys.stderr)
        return 0.0


def aggregate_monthly_flows(
    cashflow: list[QianjiRecord],
    config: Config | None = None,
) -> list[MonthlyFlowPoint]:
    """Group Qianji records by month → income, expenses, savings rate.

    Returns sorted by month ascending.
    """
    income_by_month: dict[str, float] = defaultdict(float)
    expense_by_month: dict[str, float] = defaultdict(float)

    for record in cashflow:
        date_str = str(record.get("date", ""))[:7]
        if len(date_str) != 7 or date_str[4] != "-":
            continue

        record_type = record.get("type", "")
        amount = float(record.get("amount", 0.0))

        if record_type == QJ_INCOME:
            income_by_month[date_str] += amount
        elif record_type == QJ_EXPENSE:
            expense_by_month[date_str] += amount

    all_months = sorted(set(income_by_month) | set(expense_by_month))

    points: list[MonthlyFlowPoint] = []
    for month in all_months:
        income = income_by_month.get(month, 0.0)
        expenses = expense_by_month.get(month, 0.0)
        sr = ((income - expenses) / income * 100) if income > 0 else 0.0
        points.append(MonthlyFlowPoint(month=month, income=income, expenses=expenses, savings_rate=sr))

    return points


def build_chart_data(
    data_dir: Path,
    cashflow: list[QianjiRecord] | None = None,
    config: Config | None = None,
    portfolio_total: float | None = None,
    report_date: str = "",
) -> ChartData:
    """Build all chart data from available sources.

    Parameters
    ----------
    portfolio_total
        If provided, override the latest trend point with the full portfolio
        total (Fidelity + manual assets from Qianji).  Historical points
        are Fidelity-only since we lack past Qianji snapshots.
    report_date
        Date string for the current report (YYYY-MM-DD).  Used to place
        the portfolio_total point if no matching CSV point exists.
    """
    trend = load_portfolio_totals(data_dir)

    if portfolio_total is not None:
        if trend and trend[-1].date == report_date:
            # Replace latest point with full total (includes manual assets)
            trend[-1] = SnapshotPoint(date=report_date, total=portfolio_total)
        elif report_date:
            # Add a new point for the current date
            trend.append(SnapshotPoint(date=report_date, total=portfolio_total))
            trend.sort(key=lambda p: p.date)

    flows = aggregate_monthly_flows(cashflow, config) if cashflow else []
    return ChartData(net_worth_trend=trend, monthly_flows=flows)
