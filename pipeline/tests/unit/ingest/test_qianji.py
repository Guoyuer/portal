"""Tests for Qianji CSV parser."""

from pathlib import Path

import pytest

from generate_asset_snapshot.ingest.qianji import load_cashflow


@pytest.fixture
def records(qianji_sample_csv: Path) -> list[dict]:
    """Load the fixture once for all tests."""
    return load_cashflow(qianji_sample_csv)


class TestLoadCashflow:
    def test_total_record_count(self, records: list[dict]) -> None:
        assert len(records) == 14

    def test_parses_income_record(self, records: list[dict]) -> None:
        salary_records = [r for r in records if r["category"] == "Salary" and r["amount"] == 5302.56]
        assert len(salary_records) == 1
        assert salary_records[0]["type"] == "income"

    def test_parses_expense_record(self, records: list[dict]) -> None:
        meal_records = [r for r in records if r["category"] == "Meals"]
        assert len(meal_records) >= 1
        for r in meal_records:
            assert r["type"] == "expense"

    def test_parses_transfer_record(self, records: list[dict]) -> None:
        transfers = [r for r in records if r["type"] == "transfer"]
        assert len(transfers) >= 1
        chase_to_fidelity = [
            r for r in transfers if r["account_from"] == "Chase Debit" and r["account_to"] == "Fidelity taxable"
        ]
        assert len(chase_to_fidelity) >= 1

    def test_parses_repayment_record(self, records: list[dict]) -> None:
        repayments = [r for r in records if r["type"] == "repayment"]
        assert len(repayments) >= 1
        cff_repayment = [r for r in repayments if r["account_to"] == "CFF"]
        assert len(cff_repayment) == 1

    def test_handles_cny_currency(self, records: list[dict]) -> None:
        cny_records = [r for r in records if r["currency"] == "CNY"]
        assert len(cny_records) >= 1

    def test_preserves_native_id(self, records: list[dict]) -> None:
        ids = [r["id"] for r in records]
        assert "qj1775087394054113466" in ids

    def test_handles_subcategory(self, records: list[dict]) -> None:
        r401k = [r for r in records if r["subcategory"] == "401K"]
        assert len(r401k) == 1
        grocery = [r for r in records if r["subcategory"] == "Grocery"]
        # Fixture has no Grocery subcategory, so empty subcategories should be ""
        assert len(grocery) == 0

    def test_empty_account_to_for_non_transfer(self, records: list[dict]) -> None:
        non_transfers = [r for r in records if r["type"] not in ("transfer", "repayment")]
        for r in non_transfers:
            assert r["account_to"] == ""

    def test_all_records_have_required_keys(self, records: list[dict]) -> None:
        required_keys = {
            "id",
            "date",
            "category",
            "subcategory",
            "type",
            "amount",
            "currency",
            "account_from",
            "account_to",
            "note",
        }
        for r in records:
            assert set(r.keys()) == required_keys

    def test_amount_is_float(self, records: list[dict]) -> None:
        for r in records:
            assert isinstance(r["amount"], float)

    def test_type_values_are_valid(self, records: list[dict]) -> None:
        valid_types = {"income", "expense", "transfer", "repayment"}
        for r in records:
            assert r["type"] in valid_types
