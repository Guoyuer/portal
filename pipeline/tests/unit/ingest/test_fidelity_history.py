"""Tests for Fidelity Accounts History CSV parser."""

from pathlib import Path

import pytest

from generate_asset_snapshot.ingest.fidelity_history import (
    load_transactions,
    normalize_fidelity_date,
)


class TestLoadTransactions:
    """Tests for load_transactions() using tests/fixtures/history_sample.csv."""

    @pytest.fixture()
    def transactions(self, history_sample_csv: Path) -> list[dict]:
        return load_transactions(history_sample_csv)

    # -- record count --

    def test_correct_total_record_count(self, transactions: list[dict]) -> None:
        """The fixture has 16 data rows (after 2 blank lines + header)."""
        assert len(transactions) == 16

    # -- required keys --

    def test_all_records_have_required_keys(self, transactions: list[dict]) -> None:
        required = {
            "date",
            "account",
            "action_type",
            "symbol",
            "description",
            "quantity",
            "price",
            "amount",
            "raw_action",
            "dedup_key",
        }
        for txn in transactions:
            assert required.issubset(txn.keys()), f"Missing keys in {txn}"

    # -- blank-line skipping --

    def test_skips_blank_lines_before_header(self, transactions: list[dict]) -> None:
        """The CSV has 2 blank lines before the header; parser should not choke."""
        assert len(transactions) > 0
        # First record should be the 04/02/2026 EFT deposit, normalized to ISO
        assert transactions[0]["date"] == "2026-04-02"

    # -- date normalization --

    def test_all_dates_are_iso(self, transactions: list[dict]) -> None:
        """Run dates are normalized from MM/DD/YYYY to ISO YYYY-MM-DD at ingestion."""
        import re
        iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        for txn in transactions:
            assert iso_re.match(txn["date"]), f"Non-ISO date: {txn['date']!r}"

    # -- deposit --

    def test_parses_deposit(self, transactions: list[dict]) -> None:
        deposits = [t for t in transactions if t["action_type"] == "deposit"]
        assert len(deposits) == 2
        # First deposit is $1500
        assert deposits[0]["amount"] == pytest.approx(1500.0)
        assert deposits[0]["account"] == "Z29133576"
        assert "Electronic Funds Transfer" in deposits[0]["raw_action"]

    # -- buy --

    def test_parses_buy(self, transactions: list[dict]) -> None:
        buys = [t for t in transactions if t["action_type"] == "buy"]
        assert len(buys) == 3  # AAPL, GLDM, SVIX
        aapl_buy = next(t for t in buys if t["symbol"] == "AAPL")
        assert aapl_buy["quantity"] == pytest.approx(3.0)
        assert aapl_buy["price"] == pytest.approx(252.56)
        assert aapl_buy["amount"] == pytest.approx(-757.68)

    # -- sell --

    def test_parses_sell(self, transactions: list[dict]) -> None:
        sells = [t for t in transactions if t["action_type"] == "sell"]
        assert len(sells) == 2  # SVIX and VTEB
        svix_sell = next(t for t in sells if t["symbol"] == "SVIX")
        assert svix_sell["quantity"] == pytest.approx(-20.0)
        assert svix_sell["amount"] == pytest.approx(341.2)

    # -- dividend and reinvestment --

    def test_parses_dividend(self, transactions: list[dict]) -> None:
        divs = [t for t in transactions if t["action_type"] == "dividend"]
        assert len(divs) == 2  # NVDA and QQQM
        nvda_div = next(t for t in divs if t["symbol"] == "NVDA")
        assert nvda_div["amount"] == pytest.approx(0.2)

    def test_parses_reinvestment(self, transactions: list[dict]) -> None:
        reins = [t for t in transactions if t["action_type"] == "reinvestment"]
        assert len(reins) == 2  # NVDA and QQQM
        qqqm_rein = next(t for t in reins if t["symbol"] == "QQQM")
        assert qqqm_rein["amount"] == pytest.approx(-0.98)
        assert qqqm_rein["price"] == pytest.approx(238.69)

    # -- lending and collateral --

    def test_classifies_lending(self, transactions: list[dict]) -> None:
        lending = [t for t in transactions if t["action_type"] == "lending"]
        assert len(lending) == 2  # Two LOAN RETURNED rows
        for txn in lending:
            assert "LOAN RETURNED" in txn["raw_action"]

    def test_classifies_collateral(self, transactions: list[dict]) -> None:
        collateral = [t for t in transactions if t["action_type"] == "collateral"]
        assert len(collateral) == 1
        assert "DECREASE COLLATERAL" in collateral[0]["raw_action"]

    # -- interest --

    def test_parses_interest(self, transactions: list[dict]) -> None:
        interest = [t for t in transactions if t["action_type"] == "interest"]
        assert len(interest) == 1
        assert interest[0]["amount"] == pytest.approx(1.02)
        assert interest[0]["symbol"] == "123990BL6"

    # -- foreign tax --

    def test_parses_foreign_tax(self, transactions: list[dict]) -> None:
        ftax = [t for t in transactions if t["action_type"] == "foreign_tax"]
        assert len(ftax) == 1
        assert ftax[0]["amount"] == pytest.approx(-5.67)
        assert ftax[0]["symbol"] == "TSM"

    # -- dedup_key --

    def test_dedup_key_is_tuple(self, transactions: list[dict]) -> None:
        for txn in transactions:
            assert isinstance(txn["dedup_key"], tuple), f"dedup_key should be tuple, got {type(txn['dedup_key'])}"

    def test_dedup_key_structure(self, transactions: list[dict]) -> None:
        """dedup_key is (date, account_number, raw_action, symbol, amount)."""
        txn = transactions[0]  # First EFT deposit
        key = txn["dedup_key"]
        assert len(key) == 5
        assert key == (txn["date"], txn["account"], txn["raw_action"], txn["symbol"], txn["amount"])

    def test_dedup_keys_are_unique(self, transactions: list[dict]) -> None:
        keys = [t["dedup_key"] for t in transactions]
        assert len(keys) == len(set(keys)), "Duplicate dedup_keys found"

    # -- edge: account field uses account number, not display name --

    def test_account_uses_number_not_name(self, transactions: list[dict]) -> None:
        roth_txns = [t for t in transactions if t["account"] == "238986483"]
        assert len(roth_txns) == 2  # SVIX buy and sell in ROTH IRA


class TestNormalizeFidelityDate:
    """Tests for normalize_fidelity_date() — strict MM/DD/YYYY → ISO conversion."""

    def test_happy_path(self) -> None:
        assert normalize_fidelity_date("01/15/2026") == "2026-01-15"

    def test_preserves_leading_zeros(self) -> None:
        assert normalize_fidelity_date("09/04/2026") == "2026-09-04"

    def test_end_of_year(self) -> None:
        assert normalize_fidelity_date("12/31/2025") == "2025-12-31"

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="Invalid Fidelity Run Date"):
            normalize_fidelity_date("")

    def test_rejects_one_digit_month(self) -> None:
        """Fidelity exports use zero-padded months; reject single digits."""
        with pytest.raises(ValueError, match="Invalid Fidelity Run Date"):
            normalize_fidelity_date("1/15/2026")

    def test_rejects_iso_date(self) -> None:
        """ISO format must be rejected at the Fidelity boundary."""
        with pytest.raises(ValueError, match="Invalid Fidelity Run Date"):
            normalize_fidelity_date("2026-01-15")

    def test_rejects_garbage(self) -> None:
        with pytest.raises(ValueError, match="Invalid Fidelity Run Date"):
            normalize_fidelity_date("abc")

    def test_error_message_includes_row_context(self) -> None:
        with pytest.raises(ValueError, match=r"Accounts_History\.csv row 42"):
            normalize_fidelity_date("bad", row_context="Accounts_History.csv row 42")
