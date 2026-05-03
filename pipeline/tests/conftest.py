"""Shared fixtures for all test layers."""

import sqlite3
from collections.abc import Iterator
from contextlib import suppress
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


def _close_yfinance_cache_dbs() -> None:
    """Close yfinance's peewee SQLite caches after tests that patch yfinance."""
    try:
        import yfinance.cache as yf_cache
    except Exception:
        return
    for name in ("_TzDBManager", "_CookieDBManager", "_ISINDBManager"):
        manager = getattr(yf_cache, name, None)
        if manager is None:
            continue
        with suppress(Exception):
            manager.close_db()
        if hasattr(manager, "_db"):
            manager._db = None
    for name in ("_TzCacheManager", "_CookieCacheManager", "_ISINCacheManager"):
        manager = getattr(yf_cache, name, None)
        if manager is None:
            continue
        for attr in ("_tz_cache", "_Cookie_cache", "_isin_cache"):
            if hasattr(manager, attr):
                setattr(manager, attr, None)


@pytest.fixture(autouse=True)
def _block_real_yfinance(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Unit tests must patch yfinance instead of opening network/cache handles."""
    try:
        import yfinance as yf
    except Exception:
        yield
        return

    def _blocked_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("Unit tests must patch yfinance network calls")

    monkeypatch.setattr(yf, "download", _blocked_call, raising=False)
    monkeypatch.setattr(yf, "Ticker", _blocked_call, raising=False)
    yield


@pytest.fixture(autouse=True)
def _close_third_party_caches() -> Iterator[None]:
    yield
    _close_yfinance_cache_dbs()


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
