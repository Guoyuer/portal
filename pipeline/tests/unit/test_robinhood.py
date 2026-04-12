"""Tests for Robinhood CSV parsing and replay."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from etl.ingest.robinhood_history import (
    _parse_amount,
    load_robinhood_csv,
    replay_robinhood,
)


class TestParseAmount:
    def test_positive(self) -> None:
        assert _parse_amount("$18.18") == 18.18

    def test_negative_parens(self) -> None:
        assert _parse_amount("($4.11)") == -4.11

    def test_with_commas(self) -> None:
        assert _parse_amount("$1,234.56") == 1234.56

    def test_empty(self) -> None:
        assert _parse_amount("") == 0.0


def _write_csv(path: Path, rows: list[str]) -> None:
    header = '"Activity Date","Process Date","Settle Date","Instrument","Description","Trans Code","Quantity","Price","Amount"'
    path.write_text(header + "\n" + "\n".join(rows), encoding="utf-8")


class TestLoadCsv:
    def test_parses_buy(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "rh.csv", [
            '"3/18/2026","3/18/2026","3/19/2026","UNH","UnitedHealth","Buy","0.5","$288.22","($144.11)"',
        ])
        rows = load_robinhood_csv(tmp_path / "rh.csv")
        assert len(rows) == 1
        assert rows[0]["instrument"] == "UNH"
        assert rows[0]["trans_code"] == "Buy"
        assert rows[0]["quantity"] == 0.5
        assert rows[0]["amount"] == -144.11

    def test_parses_sell(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "rh.csv", [
            '"11/3/2025","11/3/2025","11/4/2025","STUB","StubHub","Sell","1","$18.18","$18.18"',
        ])
        rows = load_robinhood_csv(tmp_path / "rh.csv")
        assert rows[0]["trans_code"] == "Sell"
        assert rows[0]["amount"] == 18.18


class TestReplayRobinhood:
    def test_single_buy(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "rh.csv", [
            '"1/10/2025","1/10/2025","1/12/2025","ARM","Arm Holdings","Buy","10","$150.00","($1500.00)"',
        ])
        result = replay_robinhood(tmp_path / "rh.csv")
        assert result["positions"]["ARM"] == pytest.approx(10.0)
        assert result["cost_basis"]["ARM"] == 1500.0

    def test_buy_and_sell(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "rh.csv", [
            '"1/10/2025","1/10/2025","","ARM","","Buy","10","$150","($1500)"',
            '"2/10/2025","2/10/2025","","ARM","","Sell","5","$200","$1000"',
        ])
        result = replay_robinhood(tmp_path / "rh.csv")
        assert result["positions"]["ARM"] == pytest.approx(5.0)
        # Sold 50%, cost basis reduced by 50%: 1500 * 0.5 = 750
        assert result["cost_basis"]["ARM"] == pytest.approx(750.0)

    def test_full_sell_removes_position(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "rh.csv", [
            '"1/10/2025","","","ARM","","Buy","10","$150","($1500)"',
            '"2/10/2025","","","ARM","","Sell","10","$200","$2000"',
        ])
        result = replay_robinhood(tmp_path / "rh.csv")
        assert "ARM" not in result["positions"]

    def test_dividends_accumulated(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "rh.csv", [
            '"1/10/2025","","","UNH","","CDIV","","","$4.11"',
            '"4/10/2025","","","UNH","","CDIV","","","$4.11"',
        ])
        result = replay_robinhood(tmp_path / "rh.csv")
        assert result["dividends"] == pytest.approx(8.22)

    def test_ach_cash(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "rh.csv", [
            '"1/5/2025","","","","Deposit","ACH","","","$500.00"',
            '"2/5/2025","","","","Withdrawal","ACH","","","($200.00)"',
        ])
        result = replay_robinhood(tmp_path / "rh.csv")
        assert result["cash"] == pytest.approx(300.0)

    def test_as_of_filters(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "rh.csv", [
            '"1/10/2025","","","ARM","","Buy","10","$150","($1500)"',
            '"6/10/2025","","","ARM","","Buy","5","$160","($800)"',
        ])
        result = replay_robinhood(tmp_path / "rh.csv", as_of=date(2025, 3, 1))
        assert result["positions"]["ARM"] == pytest.approx(10.0)
        assert result["cost_basis"]["ARM"] == 1500.0

    def test_reinvestment_buy(self, tmp_path: Path) -> None:
        _write_csv(tmp_path / "rh.csv", [
            '"1/10/2025","","","UNH","","Buy","10","$280","($2800)"',
            '"3/18/2026","","","UNH","Dividend Reinvestment","Buy","0.01426","$288.22","($4.11)"',
        ])
        result = replay_robinhood(tmp_path / "rh.csv")
        assert result["positions"]["UNH"] == pytest.approx(10.01426)
        assert result["cost_basis"]["UNH"] == pytest.approx(2804.11)
