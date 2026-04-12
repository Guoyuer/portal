"""Tests for positions CSV calibration."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from etl.db import get_connection, init_db
from etl.timemachine import (
    calibrate_from_positions,
    load_checkpoint,
)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    init_db(p)
    return p


def _write_positions_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write a mock Fidelity positions CSV."""
    headers = (
        "Account Number,Account Name,Symbol,Description,Quantity,Last Price,Last Price Change,"
        "Current Value,Today's Gain/Loss Dollar,Today's Gain/Loss Percent,Total Gain/Loss Dollar,"
        "Total Gain/Loss Percent,Percent Of Account,Cost Basis Total,Average Cost Basis,Type"
    )
    lines = [headers]
    for r in rows:
        acct = r.get("Account Number", "Z12345678")
        sym = r.get("Symbol", "")
        qty = r.get("Quantity", "0")
        cb = r.get("Cost Basis Total", "$0.00")
        # Fields: AcctNum(0), AcctName(1), Symbol(2), Desc(3), Qty(4),
        #   LastPrice(5), LastPriceChg(6), CurVal(7), TodayGLD(8), TodayGLP(9),
        #   TotalGLD(10), TotalGLP(11), PctOfAcct(12), CostBasisTotal(13), AvgCB(14), Type(15)
        # Quote cb value since it may contain commas (e.g. "$3,000.00")
        lines.append(f'{acct},,{sym},,{qty},,,,,,,,,"{cb}",,')
    path.write_text("\n".join(lines), encoding="utf-8")


REPLAY: dict[str, Any] = {
    "positions": {("Z12345678", "VOO"): 10.0, ("Z12345678", "AAPL"): 5.0},
    "cost_basis": {("Z12345678", "VOO"): 3000.0, ("Z12345678", "AAPL"): 800.0},
    "cash": {"Z12345678": 1500.0},
    "as_of": date(2025, 6, 15),
    "txn_count": 42,
}


class TestCalibration:
    def test_no_drift_when_matching(self, db: Path, tmp_path: Path) -> None:
        csv = tmp_path / "positions.csv"
        _write_positions_csv(csv, [
            {"Symbol": "VOO", "Quantity": "10", "Cost Basis Total": "$3,000.00"},
            {"Symbol": "AAPL", "Quantity": "5", "Cost Basis Total": "$800.00"},
        ])
        calibrate_from_positions(db, csv, REPLAY)
        # Check calibration log
        conn = get_connection(db)
        row = conn.execute("SELECT positions_ok, positions_total FROM calibration_log").fetchone()
        conn.close()
        assert row[0] == 2  # both match
        assert row[1] == 2

    def test_detects_cost_basis_drift(self, db: Path, tmp_path: Path) -> None:
        csv = tmp_path / "positions.csv"
        _write_positions_csv(csv, [
            {"Symbol": "VOO", "Quantity": "10", "Cost Basis Total": "$3,200.00"},  # $200 drift
            {"Symbol": "AAPL", "Quantity": "5", "Cost Basis Total": "$800.00"},
        ])
        calibrate_from_positions(db, csv, REPLAY)
        conn = get_connection(db)
        row = conn.execute("SELECT total_cb_drift, details FROM calibration_log").fetchone()
        conn.close()
        assert abs(row[0] - 200.0) < 1.0
        details = json.loads(row[1])
        assert any(d["symbol"] == "VOO" for d in details)

    def test_calibration_overwrites_replay(self, db: Path, tmp_path: Path) -> None:
        csv = tmp_path / "positions.csv"
        _write_positions_csv(csv, [
            {"Symbol": "VOO", "Quantity": "11", "Cost Basis Total": "$3,500.00"},  # different
            {"Symbol": "AAPL", "Quantity": "5", "Cost Basis Total": "$800.00"},
        ])
        result = calibrate_from_positions(db, csv, REPLAY)
        assert result["positions"][("Z12345678", "VOO")] == 11.0
        assert result["cost_basis"][("Z12345678", "VOO")] == 3500.0

    def test_saves_checkpoint(self, db: Path, tmp_path: Path) -> None:
        csv = tmp_path / "positions.csv"
        _write_positions_csv(csv, [
            {"Symbol": "VOO", "Quantity": "10", "Cost Basis Total": "$3,000.00"},
        ])
        calibrate_from_positions(db, csv, REPLAY)
        cp = load_checkpoint(db)
        assert cp is not None

    def test_removes_sold_positions(self, db: Path, tmp_path: Path) -> None:
        csv = tmp_path / "positions.csv"
        # CSV only has VOO, not AAPL (AAPL was sold)
        _write_positions_csv(csv, [
            {"Symbol": "VOO", "Quantity": "10", "Cost Basis Total": "$3,000.00"},
        ])
        result = calibrate_from_positions(db, csv, REPLAY)
        assert ("Z12345678", "AAPL") not in result["positions"]
