"""Shared fixtures for all test layers."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from etl.db import _INDEXES, _TABLES, init_db

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture
def history_sample_csv():
    return FIXTURES_DIR / "history_sample.csv"


# ── Database scaffolding ────────────────────────────────────────────────────


@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    """Schema-initialized timemachine.db under tmp_path.

    Equivalent to the ``db = tmp_path / "test.db"; init_db(db); return db``
    scaffolding repeated across ``tests/unit/``.
    """
    db = tmp_path / "test.db"
    init_db(db)
    return db


@pytest.fixture
def in_memory_db() -> Iterator[sqlite3.Connection]:
    """Schema-initialized in-memory connection, closed at teardown.

    ``init_db`` needs a file path, so we reuse the DDL modules directly for
    the ``:memory:`` case.
    """
    conn = sqlite3.connect(":memory:")
    conn.executescript(_TABLES)
    conn.executescript(_INDEXES)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()
