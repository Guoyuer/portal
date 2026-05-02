"""Tests for R2 JSON artifact export and verification."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import scripts.r2_artifacts as r2_artifacts
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
    assert manifest["objects"]["prices"]["key"] == "snapshots/2026-05-02T170000Z/prices.json"
    assert "priceRows" not in manifest["objects"]["prices"]

    timeline = json.loads((artifact_dir / "snapshots/2026-05-02T170000Z/timeline.json").read_text())
    assert timeline["daily"][0]["total"] == 1000
    assert timeline["syncMeta"] == {
        "backend": "r2",
        "version": "2026-05-02T170000Z",
        "last_sync": "2026-05-02T17:00:00Z",
        "last_date": "2026-05-01",
    }

    prices = json.loads((artifact_dir / "snapshots/2026-05-02T170000Z/prices.json").read_text())
    assert prices["VOO"]["transactions"][0]["actionType"] == "buy"

    summary = json.loads((artifact_dir / "reports/export-summary.json").read_text())
    assert summary["rowCounts"]["daily"] == 1
    assert summary["priceRowCounts"]["VOO"] == {"priceRows": 1, "transactionRows": 1}
    assert summary["objectCount"] == 3

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

    price_path = artifact_dir / "snapshots/2026-05-02T170000Z/prices.json"
    price_path.write_text('{"VOO":{"symbol":"VOO","prices":[],"transactions":[]}}', encoding="utf-8")

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


def test_baseline_parity_normalizes_publish_metadata(empty_db: Path, tmp_path: Path) -> None:
    _seed_exportable_db(empty_db)
    artifact_dir = tmp_path / "r2"
    baseline_dir = tmp_path / "baseline"
    export_artifacts(
        db_path=empty_db,
        artifact_dir=artifact_dir,
        version="2026-05-02T170000Z",
        generated_at="2026-05-02T17:00:00Z",
    )

    baseline_dir.mkdir()
    (baseline_dir / "prices").mkdir()
    timeline = json.loads((artifact_dir / "snapshots/2026-05-02T170000Z/timeline.json").read_text())
    timeline["syncMeta"] = {"last_sync": "d1-time", "last_date": "2026-05-01"}
    (baseline_dir / "timeline.json").write_text(json.dumps(timeline), encoding="utf-8")

    econ = json.loads((artifact_dir / "snapshots/2026-05-02T170000Z/econ.json").read_text())
    econ["generatedAt"] = "d1-time"
    (baseline_dir / "econ.json").write_text(json.dumps(econ), encoding="utf-8")

    prices = json.loads((artifact_dir / "snapshots/2026-05-02T170000Z/prices.json").read_text())
    (baseline_dir / "prices" / "VOO.json").write_text(json.dumps(prices["VOO"]), encoding="utf-8")

    verify_artifacts(db_path=empty_db, artifact_dir=artifact_dir, baseline_dir=baseline_dir, schema=False)
    summary = json.loads((artifact_dir / "reports/parity-summary.json").read_text())
    assert len(summary["comparisons"]) == 3


def test_remote_publish_uploads_manifest_last(empty_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_exportable_db(empty_db)
    artifact_dir = tmp_path / "r2"
    export_artifacts(
        db_path=empty_db,
        artifact_dir=artifact_dir,
        version="2026-05-02T170000Z",
        generated_at="2026-05-02T17:00:00Z",
    )

    uploaded: dict[str, bytes] = {}
    put_order: list[str] = []

    def fake_wrangler(args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
        assert capture
        op = args[0]
        key = args[1].split("/", 1)[1]
        file_arg = next((a for a in args if a.startswith("--file=")), None)
        if op == "get":
            if key not in uploaded:
                return subprocess.CompletedProcess(args, 1, "", "The specified key does not exist.")
            assert file_arg is not None
            Path(file_arg.removeprefix("--file=")).write_bytes(uploaded[key])
            return subprocess.CompletedProcess(args, 0, "", "")
        if op == "put":
            assert file_arg is not None
            uploaded[key] = Path(file_arg.removeprefix("--file=")).read_bytes()
            put_order.append(key)
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(op)

    monkeypatch.setattr(r2_artifacts, "_run_wrangler_r2", fake_wrangler)
    monkeypatch.setattr(r2_artifacts, "_LOCK_PATH", tmp_path / "publish.lock")

    r2_artifacts.publish_artifacts(db_path=empty_db, artifact_dir=artifact_dir, remote=True, schema=False)

    assert put_order[-1] == "manifest.json"
    assert "snapshots/2026-05-02T170000Z/timeline.json" in uploaded
    assert "snapshots/2026-05-02T170000Z/econ.json" in uploaded
    assert "snapshots/2026-05-02T170000Z/prices.json" in uploaded
