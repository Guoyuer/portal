"""Tests for R2 JSON artifact export and verification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from etl.db import get_connection
from scripts.r2_artifacts import export_artifacts, verify_artifacts


def _seed_exportable_db(db_path: Path) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO computed_daily "
            "(date, total, us_equity, non_us_equity, crypto, safe_net, liabilities) "
            "VALUES ('2026-05-01', 1000, 600, 100, 50, 250, 0)"
        )
        conn.execute(
            "INSERT INTO computed_daily_tickers "
            "(date, ticker, value, category, subtype, cost_basis, gain_loss, gain_loss_pct) "
            "VALUES ('2026-05-01', 'VOO', 600, 'US Equity', '', 500, 100, 0.2)"
        )
        conn.execute(
            "INSERT INTO categories (key, name, display_order, target_pct) "
            "VALUES ('US Equity', 'US Equity', 1, 0.6)"
        )
        conn.execute(
            "INSERT INTO daily_close (symbol, date, close) VALUES "
            "('VOO', '2026-05-01', 500.25)"
        )
        conn.execute(
            "INSERT INTO fidelity_transactions "
            "(run_date, account_number, action, action_type, action_kind, symbol, lot_type, quantity, price, amount) "
            "VALUES ('2026-05-01', 'A', 'YOU BOUGHT', 'buy', 'buy', 'VOO', 'Cash', 1, 500.25, -500.25)"
        )
        conn.commit()
    finally:
        conn.close()


def test_export_writes_manifest_summary_and_endpoint_artifacts(empty_db: Path, tmp_path: Path) -> None:
    _seed_exportable_db(empty_db)
    artifact_dir = tmp_path / "r2"

    manifest = export_artifacts(
        db_path=empty_db,
        artifact_dir=artifact_dir,
        version="2026-05-02T170000Z",
        generated_at="2026-05-02T17:00:00Z",
    )

    assert manifest["version"] == "2026-05-02T170000Z"
    assert manifest["objects"]["timeline"]["key"] == "snapshots/2026-05-02T170000Z/timeline.json"
    assert manifest["prices"]["VOO"]["key"] == "snapshots/2026-05-02T170000Z/prices/VOO.json"
    assert "priceRows" not in manifest["prices"]["VOO"]

    timeline = json.loads((artifact_dir / "snapshots/2026-05-02T170000Z/timeline.json").read_text())
    assert timeline["daily"][0]["total"] == 1000
    assert timeline["syncMeta"] == {
        "backend": "r2",
        "version": "2026-05-02T170000Z",
        "last_sync": "2026-05-02T17:00:00Z",
        "last_date": "2026-05-01",
    }

    price = json.loads((artifact_dir / "snapshots/2026-05-02T170000Z/prices/VOO.json").read_text())
    assert price["transactions"][0]["actionType"] == "buy"

    summary = json.loads((artifact_dir / "reports/export-summary.json").read_text())
    assert summary["rowCounts"]["daily"] == 1
    assert summary["priceRowCounts"]["VOO"] == {"priceRows": 1, "transactionRows": 1}

    verify_artifacts(db_path=empty_db, artifact_dir=artifact_dir, schema=False)


def test_verify_rejects_hash_drift(empty_db: Path, tmp_path: Path) -> None:
    _seed_exportable_db(empty_db)
    artifact_dir = tmp_path / "r2"
    export_artifacts(
        db_path=empty_db,
        artifact_dir=artifact_dir,
        version="2026-05-02T170000Z",
        generated_at="2026-05-02T17:00:00Z",
    )

    price_path = artifact_dir / "snapshots/2026-05-02T170000Z/prices/VOO.json"
    price_path.write_text('{"symbol":"VOO","prices":[],"transactions":[]}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="bytes|sha256"):
        verify_artifacts(db_path=empty_db, artifact_dir=artifact_dir, schema=False)


def test_export_rejects_path_unsafe_symbols(empty_db: Path, tmp_path: Path) -> None:
    _seed_exportable_db(empty_db)
    conn = get_connection(empty_db)
    try:
        conn.execute(
            "INSERT INTO daily_close (symbol, date, close) VALUES "
            "('BAD/SYM', '2026-05-01', 1.0)"
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="Path-unsafe"):
        export_artifacts(
            db_path=empty_db,
            artifact_dir=tmp_path / "r2",
            version="2026-05-02T170000Z",
            generated_at="2026-05-02T17:00:00Z",
        )
