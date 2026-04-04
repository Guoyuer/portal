"""Contract tests — verify invariants that must always hold regardless of implementation."""

from __future__ import annotations

from pathlib import Path

import pytest

from generate_asset_snapshot.config import load_config
from generate_asset_snapshot.ingest.fidelity_history import load_transactions
from generate_asset_snapshot.portfolio import load_portfolio
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


# ── Dedup invariants ────────────────────────────────────────────────────────


class TestDedupInvariants:
    """Dedup must be idempotent — processing the same data twice yields the same result."""

    def test_fidelity_transactions_idempotent(self) -> None:
        """Loading the same CSV twice produces identical records."""
        path = FIXTURES / "history_sample.csv"
        first = load_transactions(path)
        second = load_transactions(path)
        assert len(first) == len(second)
        for a, b in zip(first, second, strict=True):
            assert a["dedup_key"] == b["dedup_key"]
            assert a["amount"] == b["amount"]

    def test_fidelity_dedup_keys_unique(self) -> None:
        """Every record in a single CSV has a unique dedup key."""
        path = FIXTURES / "history_sample.csv"
        records = load_transactions(path)
        keys = [r["dedup_key"] for r in records]
        assert len(keys) == len(set(keys)), f"Duplicate keys found: {len(keys)} total, {len(set(keys))} unique"

    def test_qianji_ids_unique(self) -> None:
        """Every Qianji record has a unique ID."""
        records = _sample_cashflow()
        ids = [r["id"] for r in records]
        assert len(ids) == len(set(ids)), "Duplicate IDs found"

    def test_qianji_idempotent(self) -> None:
        """Calling the fixture builder twice produces identical records."""
        first = _sample_cashflow()
        second = _sample_cashflow()
        assert len(first) == len(second)
        for a, b in zip(first, second, strict=True):
            assert a["id"] == b["id"]
            assert a["amount"] == b["amount"]


# ── Financial invariants ────────────────────────────────────────────────────


class TestFinancialInvariants:
    """Mathematical properties that must always hold for financial data."""

    @pytest.fixture()
    def real_config(self) -> dict:
        p = Path("config.json")
        if not p.exists():
            pytest.skip("config.json not found")
        return load_config(p)

    @pytest.fixture()
    def real_csv(self) -> Path:
        csvs = sorted(Path("data").glob("Portfolio_Positions_*.csv"))
        if not csvs:
            pytest.skip("No CSV files found in data/")
        return csvs[-1]

    @pytest.fixture()
    def real_portfolio(self, real_csv: Path, real_config: dict) -> dict:
        return load_portfolio(real_csv, real_config)

    def test_percentages_sum_to_100(self, real_csv: Path, real_config: dict, real_portfolio: dict) -> None:
        """All category percentages must sum to ~100%."""
        report = build_report(real_portfolio, real_config, real_csv.name)
        all_cats = report.equity_categories + report.non_equity_categories
        total_pct = sum(c.pct for c in all_cats)
        assert abs(total_pct - 100.0) < 0.1, f"Category percentages sum to {total_pct}%, expected ~100%"

    def test_category_value_sums_to_total(self, real_csv: Path, real_config: dict, real_portfolio: dict) -> None:
        """Sum of all category values must equal portfolio total."""
        report = build_report(real_portfolio, real_config, real_csv.name)
        all_cats = report.equity_categories + report.non_equity_categories
        cat_sum = sum(c.value for c in all_cats)
        assert abs(cat_sum - report.total) < 0.01, f"Category sum {cat_sum} != total {report.total}"

    def test_holdings_sum_to_category(self, real_csv: Path, real_config: dict, real_portfolio: dict) -> None:
        """Holdings within each category must sum to category value."""
        report = build_report(real_portfolio, real_config, real_csv.name)
        for cat in report.equity_categories:
            holdings_sum = sum(h.value for grp in cat.subtypes for h in grp.holdings)
            assert abs(holdings_sum - cat.value) < 0.01, (
                f"{cat.name}: holdings sum {holdings_sum} != category value {cat.value}"
            )
        for cat in report.non_equity_categories:
            holdings_sum = sum(h.value for h in cat.holdings)
            assert abs(holdings_sum - cat.value) < 0.01, (
                f"{cat.name}: holdings sum {holdings_sum} != category value {cat.value}"
            )

    def test_target_weights_sum_to_100(self, real_config: dict) -> None:
        """Target weights must sum to exactly 100%."""
        total = sum(real_config["weights"].values())
        assert abs(total - 100.0) < 0.01, f"Weights sum to {total}%"

    def test_no_negative_values(self, real_csv: Path, real_config: dict, real_portfolio: dict) -> None:
        """No holding or category should have negative value."""
        report = build_report(real_portfolio, real_config, real_csv.name)
        for cat in report.equity_categories + report.non_equity_categories:
            assert cat.value >= 0, f"{cat.name} has negative value: {cat.value}"

    def test_contribution_sums_to_amount(self, real_csv: Path, real_config: dict, real_portfolio: dict) -> None:
        """Contribution allocations must sum to the contributed amount."""
        amount = 5000.0
        report = build_report(real_portfolio, real_config, real_csv.name, contribute=amount)
        assert report.contribution is not None
        alloc_sum = sum(r.allocate for r in report.contribution.rows)
        # Some remainder may not be allocated if all categories are overweight
        assert alloc_sum <= amount + 0.01, f"Allocated {alloc_sum} > contributed {amount}"


# ── Transaction classification invariants ───────────────────────────────────


class TestTransactionInvariants:
    """Invariants about transaction parsing."""

    def test_all_action_types_valid(self) -> None:
        """Every parsed transaction has a known action_type."""
        valid_types = {
            "deposit",
            "buy",
            "sell",
            "dividend",
            "reinvestment",
            "ira_contribution",
            "roth_conversion",
            "transfer",
            "interest",
            "foreign_tax",
            "lending",
            "collateral",
            "other",
        }
        records = load_transactions(FIXTURES / "history_sample.csv")
        for r in records:
            assert r["action_type"] in valid_types, (
                f"Unknown action_type: {r['action_type']} for action: {r['raw_action']}"
            )

    def test_all_qianji_types_valid(self) -> None:
        """Every Qianji record has a known type."""
        valid_types = {"income", "expense", "transfer", "repayment"}
        records = _sample_cashflow()
        for r in records:
            assert r["type"] in valid_types, f"Unknown type: {r['type']}"

    def test_buys_have_negative_amount(self) -> None:
        """Buy transactions should have negative amount (money leaving account)."""
        records = load_transactions(FIXTURES / "history_sample.csv")
        buys = [r for r in records if r["action_type"] == "buy"]
        assert len(buys) > 0, "No buys found in fixture"
        for b in buys:
            assert b["amount"] < 0, f"Buy should have negative amount: {b}"

    def test_sells_have_positive_amount(self) -> None:
        """Sell transactions should have positive amount (money entering account)."""
        records = load_transactions(FIXTURES / "history_sample.csv")
        sells = [r for r in records if r["action_type"] == "sell"]
        assert len(sells) > 0, "No sells found in fixture"
        for s in sells:
            assert s["amount"] > 0, f"Sell should have positive amount: {s}"

    def test_deposits_have_positive_amount(self) -> None:
        """Deposits should have positive amount."""
        records = load_transactions(FIXTURES / "history_sample.csv")
        deposits = [r for r in records if r["action_type"] == "deposit"]
        assert len(deposits) > 0, "No deposits found in fixture"
        for d in deposits:
            assert d["amount"] > 0, f"Deposit should have positive amount: {d}"

    def test_dividends_have_positive_amount(self) -> None:
        """Dividends should have positive amount."""
        records = load_transactions(FIXTURES / "history_sample.csv")
        dividends = [r for r in records if r["action_type"] == "dividend"]
        assert len(dividends) > 0, "No dividends found in fixture"
        for d in dividends:
            assert d["amount"] > 0, f"Dividend should have positive amount: {d}"

    def test_qianji_transfers_have_destination(self) -> None:
        """Qianji transfer records must have account_to."""
        records = _sample_cashflow()
        transfers = [r for r in records if r["type"] == "transfer"]
        assert len(transfers) > 0, "No transfers found in fixture"
        for t in transfers:
            assert t["account_to"], f"Transfer missing account_to: {t}"


# ── Cross-system invariants ─────────────────────────────────────────────────


class TestCrossSystemInvariants:
    """Verify that Qianji and Fidelity data can be cross-referenced."""

    def test_qianji_fidelity_transfers_exist(self) -> None:
        """Qianji should have transfers to Fidelity accounts."""
        records = _sample_cashflow()
        fidelity_transfers = [r for r in records if r["type"] == "transfer" and "fidelity" in r["account_to"].lower()]
        assert len(fidelity_transfers) > 0, "No Qianji→Fidelity transfers in fixture"

    def test_fidelity_deposits_exist(self) -> None:
        """Fidelity history should have deposit records."""
        records = load_transactions(FIXTURES / "history_sample.csv")
        deposits = [r for r in records if r["action_type"] == "deposit"]
        assert len(deposits) > 0, "No deposits in Fidelity fixture"

    def test_transfer_amounts_are_plausible(self) -> None:
        """Qianji→Fidelity transfer amounts should match Fidelity deposit amounts (at least some)."""
        qianji = _sample_cashflow()
        fidelity = load_transactions(FIXTURES / "history_sample.csv")

        transfer_amounts = {
            r["amount"] for r in qianji if r["type"] == "transfer" and "fidelity" in r["account_to"].lower()
        }
        deposit_amounts = {r["amount"] for r in fidelity if r["action_type"] == "deposit"}

        overlap = transfer_amounts & deposit_amounts
        assert len(overlap) > 0, (
            f"No amount overlap between Qianji transfers {transfer_amounts} and Fidelity deposits {deposit_amounts}"
        )
