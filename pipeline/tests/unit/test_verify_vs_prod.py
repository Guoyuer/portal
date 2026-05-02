"""Tests for verify_vs_prod.py parity checker."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "pipeline"))

from scripts import verify_vs_prod  # noqa: E402
from scripts.verify_vs_prod import (  # noqa: E402
    compare_daily_close_samples,
    compare_recent_totals,
    compare_row_counts,
    parse_wrangler_json,
)


def test_parse_wrangler_json():
    """wrangler d1 execute --json emits a list wrapping a 'results' array."""
    raw = json.dumps([{"results": [{"symbol": "SCHD", "close": 84.48}], "success": True}])
    rows = parse_wrangler_json(raw)
    assert rows == [{"symbol": "SCHD", "close": 84.48}]


def test_parse_wrangler_json_empty_results():
    raw = json.dumps([{"results": [], "success": True}])
    assert parse_wrangler_json(raw) == []


# ── Row counts: new semantics (local >= prod is OK) ────────────────────────

def test_compare_row_counts_match():
    """Exact match is OK."""
    result = compare_row_counts("fidelity_transactions", local=1765, prod=1765)
    assert result.ok is True
    assert "match" in result.detail


def test_compare_row_counts_local_ahead_ok():
    """Local > prod is the normal pre-sync state — must PASS."""
    result = compare_row_counts("daily_close", local=52009, prod=46197)
    assert result.ok is True
    assert "5812" in result.detail
    assert "ahead" in result.detail


def test_compare_row_counts_local_short_fails():
    """Local < prod means partial rebuild / data loss risk — must FAIL."""
    result = compare_row_counts("fidelity_transactions", local=1765, prod=1800)
    assert result.ok is False
    assert "SHORT" in result.detail
    assert "35" in result.detail


# ── daily_close samples: historical-only compare ───────────────────────────

def test_compare_daily_close_tolerance():
    """Historical rows within 0.0001 are OK."""
    local = [{"symbol": "SCHD", "date": "2024-10-01", "close": 84.4800}]
    prod = [{"symbol": "SCHD", "date": "2024-10-01", "close": 84.4801}]
    results = compare_daily_close_samples(local, prod, tolerance=0.0001, today=date(2026, 4, 12))
    assert all(r.ok for r in results)


def test_compare_daily_close_mismatch():
    """Historical row beyond tolerance must FAIL."""
    local = [{"symbol": "SCHD", "date": "2024-10-01", "close": 84.48}]
    prod = [{"symbol": "SCHD", "date": "2024-10-01", "close": 26.62}]  # Adj Close era
    results = compare_daily_close_samples(local, prod, tolerance=0.0001, today=date(2026, 4, 12))
    assert any(not r.ok for r in results)


def test_compare_daily_close_ignores_recent():
    """Rows within the (today - 7, today] window must be skipped.

    Recent prices can legitimately be re-fetched and differ; they should
    not be compared or trigger a failure.
    """
    today = date(2026, 4, 12)
    # 2026-04-10 is within the 7-day window (today - 7 = 2026-04-05)
    local = [{"symbol": "SCHD", "date": "2026-04-10", "close": 100.00}]
    prod = [{"symbol": "SCHD", "date": "2026-04-10", "close": 99.00}]  # $1 different
    results = compare_daily_close_samples(local, prod, tolerance=0.0001, today=today)
    # All results should be OK (sample skipped, not failed)
    assert all(r.ok for r in results)
    assert len(results) == 1
    assert "skipped" in results[0].detail.lower()


def test_compare_daily_close_cutoff_boundary():
    """Date exactly at today - 7 is historical (<=) and must be compared."""
    today = date(2026, 4, 12)
    # 2026-04-05 is exactly today - 7 → historical → compared
    local = [{"symbol": "SCHD", "date": "2026-04-05", "close": 100.00}]
    prod = [{"symbol": "SCHD", "date": "2026-04-05", "close": 200.00}]  # big drift
    results = compare_daily_close_samples(local, prod, tolerance=0.0001, today=today)
    assert any(not r.ok for r in results)


def test_compare_daily_close_missing_in_prod_ok():
    """Historical row only in local (not in prod) is not a drift failure.

    The row-count check catches real data loss; this is just sync lag.
    """
    today = date(2026, 4, 12)
    local = [{"symbol": "SCHD", "date": "2024-10-01", "close": 84.48}]
    prod: list[dict] = []
    results = compare_daily_close_samples(local, prod, tolerance=0.0001, today=today)
    assert all(r.ok for r in results)


# ── computed_daily totals: shared-date compare only ────────────────────────

def test_compare_recent_totals_within_dollar():
    local = [{"date": "2026-04-12", "total": 422369.00}]
    prod = [{"date": "2026-04-12", "total": 422369.50}]
    results = compare_recent_totals(local, prod, tolerance_dollars=1.0)
    assert all(r.ok for r in results)


def test_compare_recent_totals_historical_big_drift_fails():
    """Drift on an OLDER date (outside refresh window) must FAIL.

    Historical rows should be immutable — a mismatch there implies a logic
    change that would silently rewrite prod history when sync runs.
    """
    local = [{"date": "2025-01-01", "total": 422369.00}]
    prod = [{"date": "2025-01-01", "total": 411000.00}]
    results = compare_recent_totals(
        local, prod, tolerance_dollars=1.0, today=date(2026, 4, 15),
    )
    assert any(not r.ok for r in results)


def test_compare_recent_totals_refresh_window_drift_allowed():
    """Drift WITHIN the refresh window (last 7 days) is the expected flow.

    ``upsert_daily_rows`` re-writes the last REFRESH_WINDOW_DAYS on every
    build (fresh Yahoo prices) and ``sync_to_d1`` full-replaces
    ``computed_daily`` in prod. Drift in that window means local has
    fresher values that sync will cleanly propagate — not a failure.
    """
    local = [{"date": "2026-04-14", "total": 429932.33}]
    prod = [{"date": "2026-04-14", "total": 425033.92}]  # $4898 drift
    results = compare_recent_totals(
        local, prod, tolerance_dollars=1.0, today=date(2026, 4, 15),
    )
    assert all(r.ok for r in results)
    assert any("refresh window — will sync" in r.detail for r in results)


def test_compare_recent_totals_missing_in_prod_skipped():
    """Date only in local is normal pre-sync state — must NOT be a failure."""
    local = [{"date": "2026-04-12", "total": 422369.00}]
    prod: list[dict] = []  # today's value hasn't been synced yet
    results = compare_recent_totals(local, prod, tolerance_dollars=1.0)
    assert all(r.ok for r in results)
    assert any("only in local" in r.detail for r in results)


def test_compare_recent_totals_mixed_shared_and_local_only():
    """Shared dates are compared; local-only dates are skipped.

    Together they reflect the real automation state: newest date only in
    local (ok), older date in both (must match).
    """
    local = [
        {"date": "2026-04-12", "total": 422369.00},  # only in local
        {"date": "2026-04-11", "total": 421000.00},  # shared, matches
    ]
    prod = [
        {"date": "2026-04-11", "total": 421000.50},
    ]
    results = compare_recent_totals(local, prod, tolerance_dollars=1.0)
    assert all(r.ok for r in results)


# ── main() exit-code contract ──────────────────────────────────────────────


def _stub_db(tmp_path):
    """Minimal sqlite DB so the early ``_DB_PATH.exists()`` check passes."""
    import sqlite3
    db_path = tmp_path / "timemachine.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE fidelity_transactions (id INTEGER)")  # noqa: S608
    conn.execute("CREATE TABLE qianji_transactions (id INTEGER)")  # noqa: S608
    conn.execute("CREATE TABLE computed_daily (date TEXT, total REAL)")  # noqa: S608
    conn.execute("CREATE TABLE daily_close (symbol TEXT, date TEXT, close REAL)")  # noqa: S608
    conn.commit()
    conn.close()
    return db_path


def test_main_exits_2_when_wrangler_query_raises(monkeypatch, tmp_path):
    """A wrangler RuntimeError (auth/network/CLI crash) must exit with the
    INFRA code (2), NOT the drift code (1). The orchestrator translates
    these into the runner-level EXIT_PARITY_INFRA / EXIT_PARITY_FAIL."""
    db_path = _stub_db(tmp_path)
    monkeypatch.setenv("PORTAL_DB_PATH", str(db_path))
    monkeypatch.setattr(verify_vs_prod, "_DB_PATH", db_path)
    monkeypatch.setattr(
        verify_vs_prod, "run_wrangler_query",
        lambda sql: (_ for _ in ()).throw(RuntimeError("wrangler query failed (rc=1) ... 7403")),
    )
    monkeypatch.setattr(sys, "argv", ["verify_vs_prod.py"])
    with pytest.raises(SystemExit) as exc:
        verify_vs_prod.main()
    assert exc.value.code == 2


def test_main_exits_1_on_real_drift(monkeypatch, tmp_path):
    """Drift (local SHORT in a non-DIFF table) keeps the existing exit 1.

    Stubs the DB with non-empty fidelity_transactions and pretends prod has
    more rows — ``compare_row_counts`` returns ``ok=False`` and main() exits 1.
    """
    import sqlite3
    db_path = tmp_path / "timemachine.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE fidelity_transactions (id INTEGER)")  # noqa: S608
    conn.execute("CREATE TABLE qianji_transactions (id INTEGER)")  # noqa: S608
    conn.execute("CREATE TABLE computed_daily (date TEXT, total REAL)")  # noqa: S608
    conn.execute("CREATE TABLE daily_close (symbol TEXT, date TEXT, close REAL)")  # noqa: S608
    conn.execute("INSERT INTO fidelity_transactions (id) VALUES (1)")
    conn.commit()
    conn.close()
    monkeypatch.setenv("PORTAL_DB_PATH", str(db_path))
    monkeypatch.setattr(verify_vs_prod, "_DB_PATH", db_path)

    def fake_query(sql):
        if "fidelity_transactions" in sql and "COUNT" in sql:
            return [{"n": 999}]  # prod has way more → SHORT
        return []
    monkeypatch.setattr(verify_vs_prod, "run_wrangler_query", fake_query)
    monkeypatch.setattr(sys, "argv", ["verify_vs_prod.py"])
    with pytest.raises(SystemExit) as exc:
        verify_vs_prod.main()
    assert exc.value.code == 1
