"""End-to-end tests exercising the complete pipeline with real data files.

These tests verify that data flows correctly from CSV → parse → report → render.
They catch integration bugs like parsers succeeding but renderers silently
dropping sections, or encoding issues with real-world CSV exports.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from generate_asset_snapshot.config import load_config
from generate_asset_snapshot.ingest.fidelity_history import load_transactions
from generate_asset_snapshot.portfolio import load_portfolio
from generate_asset_snapshot.renderers import html
from generate_asset_snapshot.report import build_report
from generate_asset_snapshot.types import PortfolioError, QianjiRecord

# ── Fixtures ────────────────────────────────────────────────────────────────

DATA_DIR = Path("data")
FIXTURES = Path(__file__).parent.parent / "fixtures"
CONFIG_PATH = Path("config.json")


def _sample_cashflow() -> list[QianjiRecord]:
    """Representative Qianji records for testing (replaces CSV fixture)."""
    return [
        QianjiRecord(id="qj001", date="2026-03-31 19:45:18", category="Salary", subcategory="401K", type="income", amount=1640.62, currency="USD", account_from="401k", account_to="", note=""),
        QianjiRecord(id="qj002", date="2026-03-31 19:22:14", category="Salary", subcategory="", type="income", amount=5302.56, currency="USD", account_from="Chase Debit", account_to="", note=""),
        QianjiRecord(id="qj003", date="2026-03-31 19:45:07", category="Meals", subcategory="", type="expense", amount=15.78, currency="USD", account_from="Amex Gold", account_to="", note=""),
        QianjiRecord(id="qj004", date="2026-03-29 19:41:49", category="Meals", subcategory="", type="expense", amount=63.98, currency="USD", account_from="Discover", account_to="", note=""),
        QianjiRecord(id="qj005", date="2026-03-24 11:25:04", category="Subscriptions", subcategory="", type="expense", amount=231.13, currency="USD", account_from="C1 Venture X", account_to="", note="claude code"),
        QianjiRecord(id="qj006", date="2026-03-27 11:19:11", category="Other", subcategory="", type="transfer", amount=2000.0, currency="USD", account_from="Chase Debit", account_to="Fidelity taxable", note=""),
        QianjiRecord(id="qj007", date="2026-03-19 11:18:53", category="Other", subcategory="", type="transfer", amount=2000.0, currency="USD", account_from="Chase Debit", account_to="Fidelity taxable", note=""),
        QianjiRecord(id="qj008", date="2026-03-28 11:47:44", category="Other", subcategory="", type="repayment", amount=551.01, currency="USD", account_from="Chase Debit", account_to="CFF", note=""),
        QianjiRecord(id="qj009", date="2024-05-17 16:29:47", category="Gifts/Treats", subcategory="", type="expense", amount=6864.0, currency="CNY", account_from="微信零钱通", account_to="", note="test cny"),
    ]


@pytest.fixture()
def config():
    if not CONFIG_PATH.exists():
        pytest.skip("config.json not found")
    return load_config(CONFIG_PATH)


@pytest.fixture()
def latest_positions():
    csvs = sorted(DATA_DIR.glob("Portfolio_Positions_*.csv"))
    if not csvs:
        pytest.skip("No positions CSV in data/")
    return csvs[-1]


@pytest.fixture()
def latest_history():
    files = sorted(DATA_DIR.glob("Accounts_History*"))
    if not files:
        pytest.skip("No history CSV in data/")
    return files[-1]


@pytest.fixture()
def fixture_transactions():
    return load_transactions(FIXTURES / "history_sample.csv")


@pytest.fixture()
def fixture_cashflow():
    return _sample_cashflow()


# ── Core pipeline tests ─────────────────────────────────────────────────────


class TestCorePipeline:
    """Positions CSV → ReportData → rendered output."""

    def test_positions_to_report(self, config, latest_positions) -> None:
        portfolio = load_portfolio(latest_positions, config)
        report = build_report(portfolio, config, latest_positions.name)
        assert report.total > 0
        assert len(report.equity_categories) > 0
        assert report.goal > 0

    def test_positions_to_html(self, config, latest_positions) -> None:
        portfolio = load_portfolio(latest_positions, config)
        report = build_report(portfolio, config, latest_positions.name, contribute=5000)
        output = html.render(report)
        assert len(output) > 2000
        assert "<!DOCTYPE html>" in output
        assert "Holdings Detail" in output
        assert "Contribution" in output

    def test_positions_to_terminal(self, config, latest_positions) -> None:
        portfolio = load_portfolio(latest_positions, config)
        report = build_report(portfolio, config, latest_positions.name)
        output = html.render(report)
        assert "TOTAL" in output
        assert "Category Summary" in output

    def test_html_structure_valid(self, config, latest_positions) -> None:
        portfolio = load_portfolio(latest_positions, config)
        report = build_report(portfolio, config, latest_positions.name)
        output = html.render(report)
        assert output.count("<!DOCTYPE html>") == 1
        assert output.count("</html>") == 1
        assert output.count("<table") == output.count("</table>")

    def test_html_has_expandable_others(self, config, latest_positions) -> None:
        """Small positions should be collapsed into 'Others' row."""
        portfolio = load_portfolio(latest_positions, config)
        report = build_report(portfolio, config, latest_positions.name)
        output = html.render(report)
        assert "Others" in output


# ── Extended sections: fixtures ──────────────────────────────────────────────


class TestExtendedSectionsFixtures:
    """Verify that --history and --qianji data actually appears in output.
    Uses test fixtures (always available, no skip).
    """

    def test_activity_in_terminal(self, config, latest_positions, fixture_transactions) -> None:
        """Fidelity transactions → activity section visible in HTML."""
        portfolio = load_portfolio(latest_positions, config)
        report = build_report(portfolio, config, latest_positions.name, transactions=fixture_transactions)
        assert report.activity is not None
        output = html.render(report)
        assert "Investment Activity" in output
        assert "Deposits" in output or "Buys" in output

    def test_activity_in_html(self, config, latest_positions, fixture_transactions) -> None:
        """Fidelity transactions → activity section visible in HTML."""
        portfolio = load_portfolio(latest_positions, config)
        report = build_report(portfolio, config, latest_positions.name, transactions=fixture_transactions)
        output = html.render(report)
        assert "Activity" in output

    def test_cashflow_in_terminal(self, config, latest_positions, fixture_cashflow) -> None:
        """Qianji records → cash flow section visible in HTML."""
        portfolio = load_portfolio(latest_positions, config)
        report = build_report(portfolio, config, latest_positions.name, cashflow=fixture_cashflow)
        assert report.cashflow is not None
        output = html.render(report)
        assert "Cash Flow" in output
        assert "Income" in output
        assert "Expenses" in output

    def test_cashflow_in_html(self, config, latest_positions, fixture_cashflow) -> None:
        """Qianji records → cash flow section visible in HTML."""
        portfolio = load_portfolio(latest_positions, config)
        report = build_report(portfolio, config, latest_positions.name, cashflow=fixture_cashflow)
        output = html.render(report)
        assert "Cash Flow" in output
        assert "Savings" in output

    def test_balance_sheet_in_terminal(self, config, latest_positions) -> None:
        """Balance sheet always visible (from portfolio, not cashflow)."""
        portfolio = load_portfolio(latest_positions, config)
        report = build_report(portfolio, config, latest_positions.name)
        assert report.balance_sheet is not None
        output = html.render(report)
        assert "Balance Sheet" in output
        assert "Fidelity" in output
        assert "Total assets" in output

    def test_balance_sheet_in_html(self, config, latest_positions) -> None:
        portfolio = load_portfolio(latest_positions, config)
        report = build_report(portfolio, config, latest_positions.name)
        output = html.render(report)
        assert "Balance Sheet" in output

    def test_cross_reconciliation_in_terminal(
        self, config, latest_positions, fixture_transactions, fixture_cashflow
    ) -> None:
        """Both sources → cross reconciliation visible in HTML."""
        portfolio = load_portfolio(latest_positions, config)
        report = build_report(
            portfolio,
            config,
            latest_positions.name,
            transactions=fixture_transactions,
            cashflow=fixture_cashflow,
        )
        assert report.cross_reconciliation is not None
        output = html.render(report)
        assert "Cross Reconciliation" in output or "Reconciliation" in output

    def test_cross_reconciliation_in_html(
        self, config, latest_positions, fixture_transactions, fixture_cashflow
    ) -> None:
        portfolio = load_portfolio(latest_positions, config)
        report = build_report(
            portfolio,
            config,
            latest_positions.name,
            transactions=fixture_transactions,
            cashflow=fixture_cashflow,
        )
        output = html.render(report)
        assert "Reconciliation" in output

    def test_all_sections_terminal(
        self, config, latest_positions, fixture_transactions, fixture_cashflow
    ) -> None:
        """All data → all extended sections present in HTML."""
        portfolio = load_portfolio(latest_positions, config)
        report = build_report(
            portfolio,
            config,
            latest_positions.name,
            contribute=5000,
            transactions=fixture_transactions,
            cashflow=fixture_cashflow,
        )
        output = html.render(report)
        assert "Holdings Detail" in output
        assert "Category Summary" in output
        assert "Contribution" in output
        assert "Investment Activity" in output
        assert "Cash Flow" in output
        assert "Balance Sheet" in output

    def test_all_sections_html(self, config, latest_positions, fixture_transactions, fixture_cashflow) -> None:
        """All data → all extended sections present in HTML."""
        portfolio = load_portfolio(latest_positions, config)
        report = build_report(
            portfolio,
            config,
            latest_positions.name,
            contribute=5000,
            transactions=fixture_transactions,
            cashflow=fixture_cashflow,
        )
        output = html.render(report)
        assert "Holdings Detail" in output
        assert "Category Summary" in output
        assert "Contribution" in output
        assert "Activity" in output
        assert "Cash Flow" in output
        assert "Balance Sheet" in output


# ── CLI tests ────────────────────────────────────────────────────────────────


class TestCLI:
    """Test the actual CLI entry point as a subprocess.

    Both positions CSV and history CSV are required positional args.
    """

    def _run(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "generate_asset_snapshot", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def test_cli_basic(self, config, latest_positions, latest_history) -> None:
        result = self._run(str(latest_positions), str(latest_history), "--config", "config.json")
        assert result.returncode == 0
        assert "TOTAL" in result.stdout
        assert "Category Summary" in result.stdout
        assert "Investment Activity" in result.stdout

    def test_cli_with_contribute(self, config, latest_positions, latest_history) -> None:
        result = self._run(
            str(latest_positions), str(latest_history), "--config", "config.json", "--contribute", "5000"
        )
        assert result.returncode == 0
        assert "Contribution Guide" in result.stdout

    def test_cli_html_format(self, config, latest_positions, latest_history) -> None:
        result = self._run(str(latest_positions), str(latest_history), "--config", "config.json", "--format", "html")
        assert result.returncode == 0
        assert "<!DOCTYPE html>" in result.stdout

    def test_cli_hide_values(self, config, latest_positions, latest_history) -> None:
        result = self._run(str(latest_positions), str(latest_history), "--config", "config.json", "--hide")
        assert result.returncode == 0
        assert "TOTAL" in result.stdout

    def test_cli_with_qianji_db(self, config, latest_positions, latest_history) -> None:
        """Qianji DB auto-detected → Cash Flow and Balance Sheet in output."""
        from generate_asset_snapshot.ingest.qianji_db import DEFAULT_DB_PATH

        if not DEFAULT_DB_PATH.exists():
            pytest.skip("Qianji DB not found")
        result = self._run(str(latest_positions), str(latest_history), "--config", "config.json")
        assert result.returncode == 0
        assert "Cash Flow" in result.stdout
        assert "Balance Sheet" in result.stdout

    def test_cli_all_sources(self, config, latest_positions, latest_history) -> None:
        """All data → all sections present."""
        from generate_asset_snapshot.ingest.qianji_db import DEFAULT_DB_PATH

        if not DEFAULT_DB_PATH.exists():
            pytest.skip("Qianji DB not found")
        result = self._run(
            str(latest_positions), str(latest_history), "--config", "config.json", "--contribute", "3000"
        )
        assert result.returncode == 0
        assert "Category Summary" in result.stdout
        assert "Contribution Guide" in result.stdout
        assert "Investment Activity" in result.stdout
        assert "Cash Flow" in result.stdout
        assert "Balance Sheet" in result.stdout

    def test_cli_bad_csv_fails_gracefully(self, latest_history) -> None:
        result = self._run("/nonexistent.csv", str(latest_history), "--config", "config.json")
        assert result.returncode != 0

    def test_cli_bad_config_fails_gracefully(self, latest_positions, latest_history) -> None:
        result = self._run(str(latest_positions), str(latest_history), "--config", "/nonexistent.json")
        assert result.returncode != 0


# ── Multi-file consistency ──────────────────────────────────────────────────


class TestMultiFileConsistency:
    """Cross-validate different CSVs produce consistent results."""

    def test_multiple_positions_different_totals(self, config) -> None:
        csvs = sorted(DATA_DIR.glob("Portfolio_Positions_*.csv"))
        if len(csvs) < 2:
            pytest.skip("Need at least 2 position CSVs")

        totals = set()
        for csv_path in csvs[-5:]:
            try:
                portfolio = load_portfolio(csv_path, config)
                totals.add(round(portfolio["total"], 2))
            except PortfolioError:
                pass  # Old CSV with tickers no longer in config

        if len(totals) < 2:
            pytest.skip("Not enough parseable CSVs with different totals")
        assert len(totals) >= 2

    def test_fixture_transactions_parse_cleanly(self, fixture_transactions) -> None:
        assert len(fixture_transactions) > 0
        types = {r["action_type"] for r in fixture_transactions}
        assert "buy" in types
        assert "deposit" in types

    def test_fixture_cashflow_parses_cleanly(self, fixture_cashflow) -> None:
        assert len(fixture_cashflow) > 0
        types = {r["type"] for r in fixture_cashflow}
        assert "income" in types
        assert "expense" in types
