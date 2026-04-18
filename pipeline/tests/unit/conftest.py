"""Shared fixtures for unit tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_fred_api_key(monkeypatch):
    """Force `_precompute_fred` to early-return in unit tests so local runs
    don't hit the real FRED API when a developer's `.env` sets FRED_API_KEY.
    Scoped to `tests/unit/` — integration/e2e layers that legitimately need a
    key are outside this conftest. Tests that want FRED data override with
    `monkeypatch.setenv(...)` + mock `fetch_fred_data`.
    """
    monkeypatch.delenv("FRED_API_KEY", raising=False)
