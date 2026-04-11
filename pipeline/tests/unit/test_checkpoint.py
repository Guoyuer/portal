"""Tests for replay checkpoint save/load."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from generate_asset_snapshot.db import init_db
from generate_asset_snapshot.timemachine import load_checkpoint, save_checkpoint


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    init_db(p)
    return p


SAMPLE_REPLAY = {
    "positions": {("Z12345678", "VOO"): 10.5, ("Z12345678", "AAPL"): 5.0},
    "cost_basis": {("Z12345678", "VOO"): 3000.0, ("Z12345678", "AAPL"): 800.0},
    "cash": {"Z12345678": 1500.50, "Z87654321": 200.0},
    "as_of": date(2025, 6, 15),
    "txn_count": 42,
}


class TestCheckpoint:
    def test_save_load_roundtrip(self, db: Path) -> None:
        save_checkpoint(db, SAMPLE_REPLAY)
        loaded = load_checkpoint(db)
        assert loaded is not None
        assert loaded["as_of"] == date(2025, 6, 15)
        assert loaded["positions"] == {("Z12345678", "VOO"): 10.5, ("Z12345678", "AAPL"): 5.0}
        assert loaded["cost_basis"] == {("Z12345678", "VOO"): 3000.0, ("Z12345678", "AAPL"): 800.0}
        assert loaded["cash"] == {"Z12345678": 1500.50, "Z87654321": 200.0}

    def test_tuple_keys_survive_roundtrip(self, db: Path) -> None:
        save_checkpoint(db, SAMPLE_REPLAY)
        loaded = load_checkpoint(db)
        assert loaded is not None
        for key in loaded["positions"]:
            assert isinstance(key, tuple), f"Expected tuple key, got {type(key)}"
            assert len(key) == 2

    def test_load_returns_none_on_empty(self, db: Path) -> None:
        assert load_checkpoint(db) is None

    def test_latest_checkpoint_wins(self, db: Path) -> None:
        early = {**SAMPLE_REPLAY, "as_of": date(2025, 1, 1)}
        late = {**SAMPLE_REPLAY, "as_of": date(2025, 6, 15), "cash": {"X": 999.0}}
        save_checkpoint(db, early)
        save_checkpoint(db, late)
        loaded = load_checkpoint(db)
        assert loaded is not None
        assert loaded["as_of"] == date(2025, 6, 15)
        assert loaded["cash"] == {"X": 999.0}

    def test_overwrite_same_date(self, db: Path) -> None:
        save_checkpoint(db, SAMPLE_REPLAY)
        updated = {**SAMPLE_REPLAY, "cash": {"Z12345678": 9999.0}}
        save_checkpoint(db, updated)
        loaded = load_checkpoint(db)
        assert loaded is not None
        assert loaded["cash"]["Z12345678"] == 9999.0
