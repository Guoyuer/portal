"""Tests for Empower 401k DB ingestion — ``ingest_empower_qfx`` and ``ingest_empower_contributions``.

Parsing and daily interpolation live in ``etl.k401``; those are covered in
``tests/unit/test_empower_401k.py``. This file tests the DB-write side only.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from etl.db import init_db
from etl.ingest.empower_401k import (
    ingest_empower_contributions,
    ingest_empower_qfx,
)
from etl.k401 import Contribution


class TestIngestEmpower:
    @pytest.fixture()
    def db_path(self, tmp_path: Path) -> Path:
        p = tmp_path / "test.db"
        init_db(p)
        return p

    def test_ingest_qfx(self, db_path: Path, fixtures_dir: Path) -> None:
        count = ingest_empower_qfx(db_path, fixtures_dir / "qfx_sample.qfx")
        assert count == 2  # two funds in fixture
        conn = sqlite3.connect(str(db_path))
        snaps = conn.execute("SELECT COUNT(*) FROM empower_snapshots").fetchone()[0]
        funds = conn.execute("SELECT COUNT(*) FROM empower_funds").fetchone()[0]
        conn.close()
        assert snaps == 1
        assert funds == 2

    def test_idempotent_qfx(self, db_path: Path, fixtures_dir: Path) -> None:
        ingest_empower_qfx(db_path, fixtures_dir / "qfx_sample.qfx")
        ingest_empower_qfx(db_path, fixtures_dir / "qfx_sample.qfx")
        conn = sqlite3.connect(str(db_path))
        snaps = conn.execute("SELECT COUNT(*) FROM empower_snapshots").fetchone()[0]
        funds = conn.execute("SELECT COUNT(*) FROM empower_funds").fetchone()[0]
        conn.close()
        assert snaps == 1
        assert funds == 2


class TestIngestEmpowerContributions:
    @pytest.fixture()
    def db_path(self, tmp_path: Path) -> Path:
        p = tmp_path / "test.db"
        init_db(p)
        return p

    def test_ingest_contributions(self, db_path: Path) -> None:
        contribs = [
            Contribution(date=date(2025, 1, 15), amount=500.0, ticker="401k sp500"),
            Contribution(date=date(2025, 1, 15), amount=300.0, ticker="401k ex-us"),
        ]
        count = ingest_empower_contributions(db_path, contribs)
        assert count == 2

    def test_dedup_contributions(self, db_path: Path) -> None:
        contribs = [Contribution(date=date(2025, 1, 15), amount=500.0, ticker="401k sp500")]
        ingest_empower_contributions(db_path, contribs)
        count = ingest_empower_contributions(db_path, contribs)  # same again
        assert count == 1  # not doubled

    def test_empty_contributions(self, db_path: Path) -> None:
        assert ingest_empower_contributions(db_path, []) == 0
