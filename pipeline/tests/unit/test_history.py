"""Tests for historical data aggregation (chart data)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from generate_asset_snapshot.history import (
    aggregate_monthly_flows,
    build_chart_data,
    load_portfolio_totals,
)
from generate_asset_snapshot.types import (
    QJ_EXPENSE,
    QJ_INCOME,
    QJ_TRANSFER,
    QianjiRecord,
)


def _write_positions_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Symbol", "Description", "Current Value"])
        w.writeheader()
        w.writerows(rows)


def _qj(date: str, type_: str, amount: float, category: str = "") -> QianjiRecord:
    return QianjiRecord(date=date, type=type_, amount=amount, category=category, account_to="", note="")


class TestLoadPortfolioTotals:
    def test_parses_multiple_csvs(self, tmp_path):
        _write_positions_csv(tmp_path / "Portfolio_Positions_Jan-01-2026.csv", [
            {"Symbol": "VOO", "Description": "VOO", "Current Value": "$50,000.00"},
            {"Symbol": "SGOV", "Description": "SGOV", "Current Value": "$10,000.00"},
        ])
        _write_positions_csv(tmp_path / "Portfolio_Positions_Feb-01-2026.csv", [
            {"Symbol": "VOO", "Description": "VOO", "Current Value": "$55,000.00"},
        ])
        points = load_portfolio_totals(tmp_path)
        assert len(points) == 2
        assert points[0].date == "2026-01-01"
        assert points[0].total == pytest.approx(60_000)
        assert points[1].date == "2026-02-01"
        assert points[1].total == pytest.approx(55_000)

    def test_sorted_by_date(self, tmp_path):
        _write_positions_csv(tmp_path / "Portfolio_Positions_Mar-01-2026.csv", [
            {"Symbol": "VOO", "Description": "VOO", "Current Value": "$30,000.00"},
        ])
        _write_positions_csv(tmp_path / "Portfolio_Positions_Jan-01-2026.csv", [
            {"Symbol": "VOO", "Description": "VOO", "Current Value": "$10,000.00"},
        ])
        points = load_portfolio_totals(tmp_path)
        dates = [p.date for p in points]
        assert dates == sorted(dates)

    def test_skips_non_matching_files(self, tmp_path):
        _write_positions_csv(tmp_path / "random.csv", [
            {"Symbol": "VOO", "Description": "VOO", "Current Value": "$10,000.00"},
        ])
        points = load_portfolio_totals(tmp_path)
        assert len(points) == 0

    def test_skips_pending_activity(self, tmp_path):
        _write_positions_csv(tmp_path / "Portfolio_Positions_Jan-01-2026.csv", [
            {"Symbol": "VOO", "Description": "VOO", "Current Value": "$50,000.00"},
            {"Symbol": "Pending Activity", "Description": "Pending Activity", "Current Value": "$1,000.00"},
        ])
        points = load_portfolio_totals(tmp_path)
        assert points[0].total == pytest.approx(50_000)

    def test_empty_dir(self, tmp_path):
        points = load_portfolio_totals(tmp_path)
        assert len(points) == 0


class TestAggregateMonthlyFlows:
    def test_groups_by_month(self):
        records = [
            _qj("2026-01-05", QJ_INCOME, 5000, "Salary"),
            _qj("2026-01-10", QJ_EXPENSE, 1000, "Rent"),
            _qj("2026-02-05", QJ_INCOME, 5000, "Salary"),
            _qj("2026-02-10", QJ_EXPENSE, 1200, "Rent"),
        ]
        flows = aggregate_monthly_flows(records)
        assert len(flows) == 2
        assert flows[0].month == "2026-01"
        assert flows[0].income == pytest.approx(5000)
        assert flows[0].expenses == pytest.approx(1000)
        assert flows[1].month == "2026-02"

    def test_savings_rate(self):
        records = [
            _qj("2026-03-01", QJ_INCOME, 10_000, "Salary"),
            _qj("2026-03-15", QJ_EXPENSE, 4_000, "Rent"),
        ]
        flows = aggregate_monthly_flows(records)
        assert flows[0].savings_rate == pytest.approx(60.0)

    def test_zero_income_savings_rate(self):
        records = [
            _qj("2026-03-15", QJ_EXPENSE, 500, "Food"),
        ]
        flows = aggregate_monthly_flows(records)
        assert flows[0].savings_rate == pytest.approx(0.0)

    def test_ignores_transfers(self):
        records = [
            _qj("2026-03-01", QJ_INCOME, 5000, "Salary"),
            _qj("2026-03-05", QJ_TRANSFER, 2000, "Transfer"),
        ]
        flows = aggregate_monthly_flows(records)
        assert flows[0].income == pytest.approx(5000)
        assert flows[0].expenses == pytest.approx(0)

    def test_sorted_by_month(self):
        records = [
            _qj("2026-03-01", QJ_INCOME, 1000),
            _qj("2026-01-01", QJ_INCOME, 1000),
        ]
        flows = aggregate_monthly_flows(records)
        months = [f.month for f in flows]
        assert months == sorted(months)

    def test_bad_dates_skipped(self):
        records = [
            _qj("bad-date", QJ_INCOME, 1000),
            _qj("2026-03-01", QJ_INCOME, 5000, "Salary"),
        ]
        flows = aggregate_monthly_flows(records)
        assert len(flows) == 1
        assert flows[0].income == pytest.approx(5000)


class TestBuildChartData:
    def test_combines_trend_and_flows(self, tmp_path):
        _write_positions_csv(tmp_path / "Portfolio_Positions_Jan-01-2026.csv", [
            {"Symbol": "VOO", "Description": "VOO", "Current Value": "$50,000.00"},
        ])
        records = [_qj("2026-01-05", QJ_INCOME, 5000, "Salary")]
        data = build_chart_data(tmp_path, cashflow=records)
        assert len(data.net_worth_trend) == 1
        assert len(data.monthly_flows) == 1

    def test_no_cashflow(self, tmp_path):
        data = build_chart_data(tmp_path)
        assert data.monthly_flows == []

    def test_portfolio_total_override_replaces_latest(self, tmp_path):
        _write_positions_csv(tmp_path / "Portfolio_Positions_Jan-01-2026.csv", [
            {"Symbol": "VOO", "Description": "VOO", "Current Value": "$50,000.00"},
        ])
        data = build_chart_data(tmp_path, portfolio_total=80_000, report_date="2026-01-01")
        assert data.net_worth_trend[-1].total == pytest.approx(80_000)

    def test_portfolio_total_override_adds_new_point(self, tmp_path):
        _write_positions_csv(tmp_path / "Portfolio_Positions_Jan-01-2026.csv", [
            {"Symbol": "VOO", "Description": "VOO", "Current Value": "$50,000.00"},
        ])
        data = build_chart_data(tmp_path, portfolio_total=80_000, report_date="2026-02-01")
        assert len(data.net_worth_trend) == 2
        assert data.net_worth_trend[-1].date == "2026-02-01"
        assert data.net_worth_trend[-1].total == pytest.approx(80_000)
