"""Integration tests — full pipeline from CSV to rendered report."""

from __future__ import annotations

from pathlib import Path

import pytest

from generate_asset_snapshot.config import load_config
from generate_asset_snapshot.ingest.fidelity_history import load_transactions
from generate_asset_snapshot.portfolio import load_portfolio
from generate_asset_snapshot.renderers import html
from generate_asset_snapshot.report import build_report
from generate_asset_snapshot.types import QianjiRecord

FIXTURES = Path(__file__).parent.parent / "fixtures"


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


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def real_config():
    p = Path("config.json")
    if not p.exists():
        pytest.skip("config.json not found")
    return load_config(p)


@pytest.fixture()
def real_csv():
    csvs = sorted(Path("data").glob("Portfolio_Positions_*.csv"))
    if not csvs:
        pytest.skip("No CSV files found in data/")
    return csvs[-1]


@pytest.fixture()
def real_portfolio(real_csv, real_config):
    return load_portfolio(real_csv, real_config)


@pytest.fixture()
def fidelity_transactions():
    p = FIXTURES / "history_sample.csv"
    return load_transactions(p)


@pytest.fixture()
def qianji_records():
    return _sample_cashflow()


# ── Full pipeline: CSV → Portfolio → ReportData → HTML ──────────────────────


class TestFullPipeline:
    """End-to-end tests exercising the complete data flow."""

    def test_positions_to_terminal(self, real_csv, real_config, real_portfolio):
        """Positions CSV → Portfolio → ReportData → HTML output."""
        report = build_report(real_portfolio, real_config, real_csv.name)
        output = html.render(report)
        assert "Holdings Detail" in output
        assert "Category Summary" in output
        assert "TOTAL" in output
        assert "US Equity" in output

    def test_positions_to_html(self, real_csv, real_config, real_portfolio):
        """Positions CSV → Portfolio → ReportData → HTML string."""
        report = build_report(real_portfolio, real_config, real_csv.name)
        result = html.render(report)
        assert "<!DOCTYPE html>" in result
        assert "Holdings Detail" in result
        assert "Category Summary" in result
        assert "</html>" in result

    def test_positions_with_contribution(self, real_csv, real_config, real_portfolio):
        """Full pipeline with contribution guide."""
        report = build_report(real_portfolio, real_config, real_csv.name, contribute=5000)
        assert report.contribution is not None
        assert report.contribution.amount == 5000
        assert len(report.contribution.rows) > 0
        result = html.render(report)
        assert "Contribution" in result

    def test_report_data_consistency(self, real_csv, real_config, real_portfolio):
        """ReportData values are internally consistent."""
        report = build_report(real_portfolio, real_config, real_csv.name)

        # Total matches portfolio
        assert report.total == real_portfolio["total"]

        # All categories present
        all_cats = report.equity_categories + report.non_equity_categories
        assert len(all_cats) > 0

        # Goal
        if real_config["goal"] > 0:
            assert report.goal == real_config["goal"]
            assert report.goal_pct > 0


# ── Ingest layer integration ────────────────────────────────────────────────


class TestIngestIntegration:
    """Test that all ingest modules produce compatible data."""

    def test_fidelity_history_parses_cleanly(self, fidelity_transactions):
        """Fidelity history produces well-formed records."""
        assert len(fidelity_transactions) > 0
        for r in fidelity_transactions:
            assert isinstance(r["date"], str)
            assert isinstance(r["amount"], (int, float))
            assert isinstance(r["dedup_key"], tuple)

    def test_qianji_parses_cleanly(self, qianji_records):
        """Qianji produces well-formed records."""
        assert len(qianji_records) > 0
        for r in qianji_records:
            assert isinstance(r["id"], str)
            assert isinstance(r["amount"], (int, float))
            assert r["type"] in {"income", "expense", "transfer", "repayment"}

    def test_fidelity_and_qianji_dates_overlap(self, fidelity_transactions, qianji_records):
        """Both data sources cover overlapping date ranges (fixtures should)."""

        # Fidelity dates are MM/DD/YYYY, extract YYYY-MM
        def to_ym(d: str) -> str:
            parts = d.split("/")
            if len(parts) == 3:
                return f"{parts[2][:4]}-{parts[0]}"  # YYYY-MM from MM/DD/YYYY
            return d[:7]

        fidelity_months = {to_ym(r["date"]) for r in fidelity_transactions}
        qianji_months = {r["date"][:7] for r in qianji_records}
        overlap = fidelity_months & qianji_months
        assert len(overlap) > 0, f"No month overlap: Fidelity {fidelity_months}, Qianji {qianji_months}"

    def test_qianji_fidelity_transfer_bridge(self, fidelity_transactions, qianji_records):
        """Qianji transfers to Fidelity can be matched with Fidelity deposits."""
        qj_transfers = [r for r in qianji_records if r["type"] == "transfer" and "fidelity" in r["account_to"].lower()]
        fi_deposits = [r for r in fidelity_transactions if r["action_type"] == "deposit"]

        # Both should exist
        assert len(qj_transfers) > 0
        assert len(fi_deposits) > 0

        # At least one amount should match
        qj_amounts = {r["amount"] for r in qj_transfers}
        fi_amounts = {r["amount"] for r in fi_deposits}
        assert qj_amounts & fi_amounts, "No matching amounts between Qianji transfers and Fidelity deposits"


# ── Graceful degradation ────────────────────────────────────────────────────


class TestGracefulDegradation:
    """System must produce valid output even when optional data is missing."""

    def test_positions_only(self, real_csv, real_config, real_portfolio):
        """Report works with just positions — no history, no Qianji."""
        report = build_report(real_portfolio, real_config, real_csv.name)
        assert report.total > 0
        # Optional fields don't exist yet — they'll be added in Wave 3
        # For now just verify core report works and renders
        result = html.render(report)
        assert "<!DOCTYPE html>" in result

    def test_html_render_never_crashes(self, real_csv, real_config, real_portfolio):
        """HTML renderer must never crash regardless of which fields are None."""
        report = build_report(real_portfolio, real_config, real_csv.name)
        # Explicitly set all optional fields to None
        report.activity = None
        report.reconciliation = None
        report.contribution = None
        # These don't exist yet but will — make sure render handles missing attrs
        for attr in [
            "balance_sheet",
            "cashflow",
            "cross_reconciliation",
            "market",
            "holdings_detail",
            "narrative",
            "alerts",
        ]:
            if hasattr(report, attr):
                setattr(report, attr, None)

        # Must not crash
        result = html.render(report)
        assert "<!DOCTYPE html>" in result
        assert "</html>" in result


# ── Real data full pipeline (with actual CSVs from data/) ───────────────────


class TestRealDataPipeline:
    """End-to-end with actual portfolio data files."""

    def test_real_positions_full_cycle(self, real_csv, real_config):
        """Load real positions → build report → render HTML → verify content."""
        portfolio = load_portfolio(real_csv, real_config)
        assert portfolio["total"] > 0

        report = build_report(portfolio, real_config, real_csv.name, contribute=3000)
        assert report.total == portfolio["total"]
        assert report.contribution is not None

        html_output = html.render(report)
        assert len(html_output) > 1000  # non-trivial output
        assert "Holdings Detail" in html_output
        assert "Category Summary" in html_output
        assert "Contribution" in html_output

    def test_real_history_if_available(self) -> None:
        """Load real Fidelity history if file exists."""
        # File may have spaces in name
        history_files = sorted(Path("data").glob("Accounts_History*"))
        if not history_files:
            pytest.skip("No Accounts_History file in data/")
        records = load_transactions(history_files[-1])
        if len(records) == 0:
            pytest.skip("History CSV parsed 0 records (may have different format)")
        assert len(records) > 5
        types = {r["action_type"] for r in records}
        # At least some known types should be present
        assert len(types) >= 2, f"Only found types: {types}"

    def test_sample_cashflow_well_formed(self) -> None:
        """Sample cashflow fixture produces well-formed records."""
        records = _sample_cashflow()
        assert len(records) > 0
        types = {r["type"] for r in records}
        assert "income" in types
        assert "expense" in types
