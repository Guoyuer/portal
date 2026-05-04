"""Tests for the compact automation email summary."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from etl.automation.receipt import (
    NetWorthPoint,
    PublishSummary,
    SyncReceipt,
    SyncSnapshot,
    build_subject,
    capture,
    format_html,
    format_text,
    load_publish_summary,
)
from etl.db import init_db
from tests.fixtures import connected_db, insert_computed_daily


def _make_db() -> Path:
    tmp = Path(tempfile.mktemp(suffix=".db"))
    init_db(tmp)
    return tmp


def _ctx(**overrides: object) -> dict[str, object]:
    ctx: dict[str, object] = {
        "timestamp": "2026-05-02 12:00",
        "status_label": "OK",
        "exit_code": 0,
        "log_file": "/tmp/sync.log",
        "warnings": [],
        "duration": "42s",
    }
    ctx.update(overrides)
    return ctx


def _summary() -> PublishSummary:
    return PublishSummary(
        version="2026-05-02T120000Z",
        generated_at="2026-05-02T12:00:00Z",
        latest_date="2026-05-01",
        total_bytes=8_024_559,
        object_count=3,
        price_symbols=105,
        price_rows=50_753,
        price_transaction_rows=1_560,
    )


def test_capture_reads_latest_net_worth() -> None:
    db = _make_db()
    with connected_db(db) as conn:
        insert_computed_daily(conn, "2026-05-01", 1000, liabilities=-50)

    snap = capture(db)

    assert snap.net_worth is not None
    assert snap.net_worth.date == "2026-05-01"
    assert snap.net_worth.value == 950


def test_capture_missing_db_is_empty(tmp_path: Path) -> None:
    snap = capture(tmp_path / "missing.db")
    assert snap.net_worth is None


def test_load_publish_summary(tmp_path: Path) -> None:
    path = tmp_path / "export-summary.json"
    path.write_text(
        json.dumps({
            "version": "v1",
            "generatedAt": "now",
            "objectCount": 3,
            "totalBytes": 4096,
            "source": {"latestDate": "2026-05-01"},
            "priceRowCounts": {
                "VOO": {"priceRows": 10, "transactionRows": 2},
                "SPAXX": {"priceRows": 0, "transactionRows": 3},
            },
        }),
        encoding="utf-8",
    )

    summary = load_publish_summary(path)

    assert summary == PublishSummary(
        version="v1",
        generated_at="now",
        latest_date="2026-05-01",
        total_bytes=4096,
        object_count=3,
        price_symbols=2,
        price_rows=10,
        price_transaction_rows=5,
    )


def test_format_text_success_receipt() -> None:
    cl = SyncReceipt(
        before=SyncSnapshot(
            net_worth=NetWorthPoint("2026-04-30", 1000),
        ),
        after=SyncSnapshot(
            net_worth=NetWorthPoint("2026-05-01", 1100),
        ),
    )

    body = format_text(cl, _ctx(publish_summary=_summary()))

    assert "Version: 2026-05-02T120000Z" in body
    assert "Latest date: 2026-05-01" in body
    assert "Publish: remote" in body
    assert "Prices: 105 symbols, 50,753 price rows, 1,560 transaction rows" in body
    assert "Net worth: 2026-04-30 $1,000.00 -> 2026-05-01 $1,100.00 (+$100.00 / +10.00%)" in body
    assert "Duration: 42s" in body


def test_format_text_failure() -> None:
    body = format_text(
        SyncReceipt(),
        _ctx(exit_code=2, status_label="ARTIFACT VERIFY FAILED", error="r2_artifacts.py verify exited with code 1"),
    )

    assert "Status: ARTIFACT VERIFY FAILED" in body
    assert "Blocked at: artifact verification (r2_artifacts.py)" in body
    assert "r2_artifacts.py verify" in body
    assert "Duration: 42s" in body


def test_format_text_warnings_and_dry_run() -> None:
    body = format_text(
        SyncReceipt(),
        _ctx(publish_summary=_summary(), dry_run=True, warnings=["date gap"]),
    )

    assert "Publish: skipped (dry-run)" in body
    assert "Warnings" in body
    assert "* date gap" in body


def test_format_html_escapes_text() -> None:
    html = format_html(SyncReceipt(), _ctx(exit_code=1, error="bad <tag> & data"))
    assert "<pre>" in html
    assert "bad &lt;tag&gt; &amp; data" in html


def test_build_subject_success_and_failure() -> None:
    cl = SyncReceipt(
        before=SyncSnapshot(net_worth=NetWorthPoint("2026-04-30", 1000)),
        after=SyncSnapshot(net_worth=NetWorthPoint("2026-05-01", 1100)),
    )
    assert build_subject(cl, 0, publish_summary=_summary()) == (
        "[Portal Sync] OK - 2026-05-01, nw +$100.00"
    )
    assert build_subject(SyncReceipt(), 1, "BUILD FAILED") == "[Portal Sync] FAIL - BUILD FAILED"
    assert build_subject(SyncReceipt(), 99) == "[Portal Sync] FAIL (exit 99)"
