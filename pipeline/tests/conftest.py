"""Shared fixtures for all test layers."""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture
def history_sample_csv():
    return FIXTURES_DIR / "history_sample.csv"


@pytest.fixture(autouse=True)
def _no_fred_api_key(monkeypatch):
    """Force `_precompute_fred` to early-return across all tests so local runs
    don't hit the real FRED API when a developer's `.env` happens to set
    FRED_API_KEY. Tests that want FRED data mock `fetch_fred_data` directly.
    """
    monkeypatch.delenv("FRED_API_KEY", raising=False)
