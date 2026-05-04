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
            "(date, ticker, value, category, subtype) "
            "VALUES ('2026-05-01', 'VOO', 600, 'US Equity', '')"
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
        conn.execute(
            "INSERT INTO computed_market_indices "
            "(ticker, name, current, month_return, ytd_return, high_52w, low_52w, sparkline) "
            "VALUES ('^GSPC', 'S&P 500', 5000, 1.2, 3.4, 5100, 4000, '[4900,5000]')"
        )
        conn.execute(
            "INSERT INTO qianji_transactions "
            "(date, type, category, amount, is_retirement, account_to) "
            "VALUES ('2026-05-01', 'income', '401K', 100, 1, '')"
        )
        conn.execute(
            "INSERT INTO econ_series (key, date, value) VALUES "
            "('fedFundsRate', '2026-04', 4.5), "
            "('fedFundsRate', '2026-05', 4.25)"
        )
        conn.commit()
    finally:
        conn.close()


def test_export_writes_manifest_summary_and_endpoint_artifacts(
    empty_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_exportable_db(empty_db)
    artifact_dir = tmp_path / "r2"
    monkeypatch.setattr(r2_artifacts, "_run_schema_check", lambda _artifact_dir: None)

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
    assert "errors" not in timeline
    assert timeline["market"]["indices"][0]["sparkline"] == [4900, 5000]
    assert timeline["qianjiTxns"][0]["isRetirement"] is True
    assert timeline["dailyTickers"][0] == {
        "date": "2026-05-01",
        "ticker": "VOO",
        "value": 600,
        "category": "US Equity",
        "subtype": "",
    }
    assert timeline["categories"][0] == {"key": "US Equity", "name": "US Equity", "targetPct": 0.6}
    assert timeline["syncMeta"] == {"last_sync": "2026-05-02T17:00:00Z"}

    prices = json.loads((artifact_dir / "snapshots/2026-05-02T170000Z/prices.json").read_text())
    assert prices["VOO"]["transactions"][0]["actionType"] == "buy"

    econ = json.loads((artifact_dir / "snapshots/2026-05-02T170000Z/econ.json").read_text())
    assert econ["series"]["fedFundsRate"] == [
        {"date": "2026-04", "value": 4.5},
        {"date": "2026-05", "value": 4.25},
    ]

    summary = json.loads((artifact_dir / "reports/export-summary.json").read_text())
    assert summary["rowCounts"]["daily"] == 1
    assert summary["priceRowCounts"]["VOO"] == {"priceRows": 1, "transactionRows": 1}
    assert summary["objectCount"] == 3

    verify_artifacts(db_path=empty_db, artifact_dir=artifact_dir)


def test_verify_rejects_hash_drift(
    empty_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_exportable_db(empty_db)
    artifact_dir = tmp_path / "r2"
    monkeypatch.setattr(r2_artifacts, "_run_schema_check", lambda _artifact_dir: None)
    export_artifacts(
        db_path=empty_db,
        artifact_dir=artifact_dir,
        version="2026-05-02T170000Z",
        generated_at="2026-05-02T17:00:00Z",
    )

    price_path = artifact_dir / "snapshots/2026-05-02T170000Z/prices.json"
    price_path.write_text('{"VOO":{"symbol":"VOO","prices":[],"transactions":[]}}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="bytes|sha256"):
        verify_artifacts(db_path=empty_db, artifact_dir=artifact_dir)


def test_export_allows_symbols_that_are_only_json_keys(empty_db: Path, tmp_path: Path) -> None:
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

    artifact_dir = tmp_path / "r2"
    export_artifacts(
        db_path=empty_db,
        artifact_dir=artifact_dir,
        version="2026-05-02T170000Z",
        generated_at="2026-05-02T17:00:00Z",
    )

    prices = json.loads((artifact_dir / "snapshots/2026-05-02T170000Z/prices.json").read_text())
    assert prices["BAD/SYM"]["prices"] == [{"close": 1.0, "date": "2026-05-01"}]


@pytest.mark.parametrize(("remote", "mode_flag"), [(True, "--remote"), (False, "--local")])
def test_publish_uploads_manifest_last(
    empty_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote: bool,
    mode_flag: str,
) -> None:
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

    def fake_wrangler(
        op: str,
        key: str,
        *,
        remote: bool,
        file_path: Path | None = None,
        content_type: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert ("--remote" if remote else "--local") == mode_flag
        assert key
        if op == "get":
            if key not in uploaded:
                return subprocess.CompletedProcess([op, key], 1, "", "The specified key does not exist.")
            assert file_path is not None
            file_path.write_bytes(uploaded[key])
            return subprocess.CompletedProcess([op, key], 0, "", "")
        if op == "put":
            assert file_path is not None
            assert content_type == "application/json"
            uploaded[key] = file_path.read_bytes()
            put_order.append(key)
            return subprocess.CompletedProcess([op, key], 0, "", "")
        raise AssertionError(op)

    monkeypatch.setattr(r2_artifacts, "_run_wrangler_r2", fake_wrangler)
    monkeypatch.setattr(r2_artifacts, "_LOCK_PATH", tmp_path / "publish.lock")
    monkeypatch.setattr(r2_artifacts, "_run_schema_check", lambda _artifact_dir: None)

    r2_artifacts.publish_artifacts(db_path=empty_db, artifact_dir=artifact_dir, remote=remote)

    assert put_order[-1] == "manifest.json"
    assert "snapshots/2026-05-02T170000Z/timeline.json" in uploaded
    assert "snapshots/2026-05-02T170000Z/econ.json" in uploaded
    assert "snapshots/2026-05-02T170000Z/prices.json" in uploaded
