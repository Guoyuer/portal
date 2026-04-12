"""Tests for category metadata pipeline: config → categories table → v_categories view."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from generate_asset_snapshot.categories import (
    CATEGORY_NAME_TO_KEY,
    ingest_categories,
)
from generate_asset_snapshot.db import init_db


def _make_db() -> Path:
    tmp = Path(tempfile.mktemp(suffix=".db"))
    init_db(tmp)
    return tmp


class TestCategoryNameToKey:
    def test_four_required_categories(self) -> None:
        assert CATEGORY_NAME_TO_KEY["US Equity"] == "usEquity"
        assert CATEGORY_NAME_TO_KEY["Non-US Equity"] == "nonUsEquity"
        assert CATEGORY_NAME_TO_KEY["Crypto"] == "crypto"
        assert CATEGORY_NAME_TO_KEY["Safe Net"] == "safeNet"


class TestIngestCategories:
    def test_writes_rows_from_config(self) -> None:
        db = _make_db()
        try:
            config = {
                "target_weights": {
                    "US Equity": 55,
                    "Non-US Equity": 15,
                    "Crypto": 3,
                    "Safe Net": 27,
                },
                "category_order": ["US Equity", "Non-US Equity", "Crypto", "Safe Net"],
            }
            count = ingest_categories(db, config)
            assert count == 4
            conn = sqlite3.connect(db)
            rows = conn.execute(
                "SELECT key, name, display_order, target_pct FROM categories ORDER BY display_order"
            ).fetchall()
            conn.close()
            assert rows == [
                ("usEquity", "US Equity", 0, 55.0),
                ("nonUsEquity", "Non-US Equity", 1, 15.0),
                ("crypto", "Crypto", 2, 3.0),
                ("safeNet", "Safe Net", 3, 27.0),
            ]
        finally:
            db.unlink(missing_ok=True)

    def test_replaces_existing_rows(self) -> None:
        """A second ingest should overwrite stale rows, not append."""
        db = _make_db()
        try:
            cfg1 = {
                "target_weights": {"US Equity": 55, "Non-US Equity": 15, "Crypto": 3, "Safe Net": 27},
                "category_order": ["US Equity", "Non-US Equity", "Crypto", "Safe Net"],
            }
            ingest_categories(db, cfg1)
            cfg2 = {
                "target_weights": {"US Equity": 60, "Non-US Equity": 10, "Crypto": 5, "Safe Net": 25},
                "category_order": ["US Equity", "Non-US Equity", "Crypto", "Safe Net"],
            }
            count = ingest_categories(db, cfg2)
            assert count == 4
            conn = sqlite3.connect(db)
            rows = conn.execute(
                "SELECT key, target_pct FROM categories ORDER BY display_order"
            ).fetchall()
            conn.close()
            assert rows == [
                ("usEquity", 60.0),
                ("nonUsEquity", 10.0),
                ("crypto", 5.0),
                ("safeNet", 25.0),
            ]
        finally:
            db.unlink(missing_ok=True)

    def test_ignores_unknown_category_name(self) -> None:
        """A category in config that we don't have a key for is skipped (not a pipeline crash)."""
        db = _make_db()
        try:
            config = {
                "target_weights": {"US Equity": 55, "Alternative": 10, "Non-US Equity": 15, "Crypto": 3, "Safe Net": 17},
                "category_order": ["US Equity", "Alternative", "Non-US Equity", "Crypto", "Safe Net"],
            }
            count = ingest_categories(db, config)
            # Only 4 known mapped names
            assert count == 4
            conn = sqlite3.connect(db)
            names = [r[0] for r in conn.execute("SELECT name FROM categories ORDER BY display_order").fetchall()]
            conn.close()
            assert names == ["US Equity", "Non-US Equity", "Crypto", "Safe Net"]
        finally:
            db.unlink(missing_ok=True)

    def test_defaults_zero_target_if_weight_missing(self) -> None:
        """If target_weights lacks a key we still map, target is 0."""
        db = _make_db()
        try:
            config = {
                "target_weights": {"US Equity": 55, "Non-US Equity": 15, "Safe Net": 30},
                "category_order": ["US Equity", "Non-US Equity", "Crypto", "Safe Net"],
            }
            ingest_categories(db, config)
            conn = sqlite3.connect(db)
            crypto = conn.execute(
                "SELECT target_pct FROM categories WHERE key = 'crypto'"
            ).fetchone()
            conn.close()
            assert crypto == (0.0,)
        finally:
            db.unlink(missing_ok=True)


class TestVCategoriesView:
    def test_view_exposes_camelcase_columns(self) -> None:
        db = _make_db()
        try:
            config = {
                "target_weights": {"US Equity": 55, "Non-US Equity": 15, "Crypto": 3, "Safe Net": 27},
                "category_order": ["US Equity", "Non-US Equity", "Crypto", "Safe Net"],
            }
            ingest_categories(db, config)
            conn = sqlite3.connect(db)
            cur = conn.execute("SELECT key, name, displayOrder, targetPct FROM v_categories ORDER BY displayOrder")
            rows = cur.fetchall()
            conn.close()
            assert rows[0] == ("usEquity", "US Equity", 0, 55.0)
            assert rows[3] == ("safeNet", "Safe Net", 3, 27.0)
        finally:
            db.unlink(missing_ok=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
