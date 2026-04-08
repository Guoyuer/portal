"""Tests for replay() cost basis tracking."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from generate_asset_snapshot.timemachine import replay


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write a minimal Fidelity-format CSV."""
    header = "Run Date,Account,Account Number,Action,Symbol,Description,Type,Quantity,Price,Amount,Settlement Date"
    with open(path, "w", newline="") as f:
        f.write(header + "\n")
        writer = csv.DictWriter(f, fieldnames=header.split(","))
        for row in rows:
            full = {k: row.get(k, "") for k in header.split(",")}
            writer.writerow(full)


class TestReplayCostBasis:
    def test_single_buy(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "txns.csv"
        _write_csv(csv_path, [
            {"Run Date": "01/02/2025", "Account Number": "Z123", "Action": "YOU BOUGHT VANGUARD",
             "Symbol": "VOO", "Quantity": "10", "Price": "500", "Amount": "-5000"},
        ])
        result = replay(csv_path, date(2025, 1, 2))
        assert ("Z123", "VOO") in result["cost_basis"]
        assert result["cost_basis"][("Z123", "VOO")] == 5000.0

    def test_multiple_buys_accumulate(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "txns.csv"
        _write_csv(csv_path, [
            {"Run Date": "01/02/2025", "Account Number": "Z123", "Action": "YOU BOUGHT X",
             "Symbol": "VOO", "Quantity": "10", "Price": "500", "Amount": "-5000"},
            {"Run Date": "01/03/2025", "Account Number": "Z123", "Action": "YOU BOUGHT X",
             "Symbol": "VOO", "Quantity": "5", "Price": "510", "Amount": "-2550"},
        ])
        result = replay(csv_path, date(2025, 1, 3))
        assert result["cost_basis"][("Z123", "VOO")] == pytest.approx(7550.0)

    def test_sell_reduces_cost_basis_proportionally(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "txns.csv"
        _write_csv(csv_path, [
            {"Run Date": "01/02/2025", "Account Number": "Z123", "Action": "YOU BOUGHT X",
             "Symbol": "VOO", "Quantity": "10", "Price": "500", "Amount": "-5000"},
            # Sell 50% of position
            {"Run Date": "01/03/2025", "Account Number": "Z123", "Action": "YOU SOLD X",
             "Symbol": "VOO", "Quantity": "-5", "Price": "600", "Amount": "3000"},
        ])
        result = replay(csv_path, date(2025, 1, 3))
        # Sold 5/10 = 50%, so cost basis reduced by 50%: 5000 * 0.5 = 2500
        assert result["cost_basis"][("Z123", "VOO")] == pytest.approx(2500.0)
        assert result["positions"][("Z123", "VOO")] == pytest.approx(5.0)

    def test_full_sell_zeroes_cost_basis(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "txns.csv"
        _write_csv(csv_path, [
            {"Run Date": "01/02/2025", "Account Number": "Z123", "Action": "YOU BOUGHT X",
             "Symbol": "VOO", "Quantity": "10", "Price": "500", "Amount": "-5000"},
            {"Run Date": "01/03/2025", "Account Number": "Z123", "Action": "YOU SOLD X",
             "Symbol": "VOO", "Quantity": "-10", "Price": "600", "Amount": "6000"},
        ])
        result = replay(csv_path, date(2025, 1, 3))
        assert result["cost_basis"].get(("Z123", "VOO"), 0) == pytest.approx(0.0)

    def test_reinvestment_adds_cost(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "txns.csv"
        _write_csv(csv_path, [
            {"Run Date": "01/02/2025", "Account Number": "Z123", "Action": "YOU BOUGHT X",
             "Symbol": "VOO", "Quantity": "10", "Price": "500", "Amount": "-5000"},
            {"Run Date": "01/03/2025", "Account Number": "Z123", "Action": "REINVESTMENT",
             "Symbol": "VOO", "Quantity": "0.5", "Price": "500", "Amount": "-250"},
        ])
        result = replay(csv_path, date(2025, 1, 3))
        assert result["cost_basis"][("Z123", "VOO")] == pytest.approx(5250.0)

    def test_money_market_excluded_from_cost_basis(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "txns.csv"
        _write_csv(csv_path, [
            {"Run Date": "01/02/2025", "Account Number": "Z123", "Action": "YOU BOUGHT X",
             "Symbol": "SPAXX", "Quantity": "1000", "Price": "1", "Amount": "-1000"},
        ])
        result = replay(csv_path, date(2025, 1, 2))
        assert ("Z123", "SPAXX") not in result["cost_basis"]

    def test_cost_basis_respects_as_of(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "txns.csv"
        _write_csv(csv_path, [
            {"Run Date": "01/02/2025", "Account Number": "Z123", "Action": "YOU BOUGHT X",
             "Symbol": "VOO", "Quantity": "10", "Price": "500", "Amount": "-5000"},
            {"Run Date": "01/05/2025", "Account Number": "Z123", "Action": "YOU BOUGHT X",
             "Symbol": "VOO", "Quantity": "5", "Price": "510", "Amount": "-2550"},
        ])
        result = replay(csv_path, date(2025, 1, 3))
        # Only first buy should be counted
        assert result["cost_basis"][("Z123", "VOO")] == pytest.approx(5000.0)

    def test_multiple_accounts(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "txns.csv"
        _write_csv(csv_path, [
            {"Run Date": "01/02/2025", "Account Number": "Z123", "Action": "YOU BOUGHT X",
             "Symbol": "VOO", "Quantity": "10", "Price": "500", "Amount": "-5000"},
            {"Run Date": "01/02/2025", "Account Number": "Z456", "Action": "YOU BOUGHT X",
             "Symbol": "VOO", "Quantity": "5", "Price": "500", "Amount": "-2500"},
        ])
        result = replay(csv_path, date(2025, 1, 2))
        assert result["cost_basis"][("Z123", "VOO")] == 5000.0
        assert result["cost_basis"][("Z456", "VOO")] == 2500.0
