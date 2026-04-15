"""Contract tests — verify invariants that must always hold regardless of implementation."""

from __future__ import annotations

from pathlib import Path

from etl.sources.fidelity import load_transactions
from etl.types import QianjiRecord

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
