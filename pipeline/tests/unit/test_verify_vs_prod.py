"""Tests for verify_vs_prod.py parity checker."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "pipeline"))

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


def test_compare_row_counts_match():
    result = compare_row_counts("fidelity_transactions", local=1765, prod=1765)
    assert result.ok is True
    assert result.table == "fidelity_transactions"


def test_compare_row_counts_mismatch():
    result = compare_row_counts("fidelity_transactions", local=1765, prod=1800)
    assert result.ok is False
    assert "-35" in result.detail or "35" in result.detail


def test_compare_daily_close_tolerance():
    """Within 0.0001 is OK."""
    local = [{"symbol": "SCHD", "date": "2024-10-01", "close": 84.4800}]
    prod = [{"symbol": "SCHD", "date": "2024-10-01", "close": 84.4801}]
    results = compare_daily_close_samples(local, prod, tolerance=0.0001)
    assert all(r.ok for r in results)


def test_compare_daily_close_mismatch():
    """Beyond tolerance is not OK."""
    local = [{"symbol": "SCHD", "date": "2024-10-01", "close": 84.48}]
    prod = [{"symbol": "SCHD", "date": "2024-10-01", "close": 26.62}]  # Adj Close era
    results = compare_daily_close_samples(local, prod, tolerance=0.0001)
    assert any(not r.ok for r in results)


def test_compare_recent_totals_within_dollar():
    local = [{"date": "2026-04-12", "total": 422369.00}]
    prod = [{"date": "2026-04-12", "total": 422369.50}]
    results = compare_recent_totals(local, prod, tolerance_dollars=1.0)
    assert all(r.ok for r in results)


def test_compare_recent_totals_big_drift():
    local = [{"date": "2026-04-12", "total": 422369.00}]
    prod = [{"date": "2026-04-12", "total": 411000.00}]
    results = compare_recent_totals(local, prod, tolerance_dollars=1.0)
    assert any(not r.ok for r in results)
