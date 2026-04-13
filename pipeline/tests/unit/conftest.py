"""Shared fixtures for unit tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from etl.config import load_config
from etl.types import Config

MINIMAL_CONFIG_DATA = {
    "assets": {
        "VOO": {"category": "US Equity", "subtype": "broad"},
        "QQQM": {"category": "US Equity", "subtype": "growth"},
        "VXUS": {"category": "Non-US Equity", "subtype": "broad"},
        "FBTC": {"category": "Crypto"},
        "SGOV": {"category": "Safe Net"},
        "VGLT": {"category": "Safe Net"},
    },
    "target_weights": {
        "US Equity": 55,
        "Non-US Equity": 15,
        "Crypto": 5,
        "Safe Net": 25,
    },
    "category_order": ["US Equity", "Non-US Equity", "Crypto", "Safe Net"],
}

ALL_TICKERS_ROWS = [
    {"Symbol": "VOO", "Description": "VOO", "Current Value": "$1,000.00"},
    {"Symbol": "QQQM", "Description": "QQQM", "Current Value": "$1,000.00"},
    {"Symbol": "VXUS", "Description": "VXUS", "Current Value": "$1,000.00"},
    {"Symbol": "FBTC", "Description": "FBTC", "Current Value": "$1,000.00"},
    {"Symbol": "SGOV", "Description": "SGOV", "Current Value": "$1,000.00"},
    {"Symbol": "VGLT", "Description": "VGLT", "Current Value": "$1,000.00"},
]


def load_test_config(tmp_path: Path, data: dict) -> Config:
    """Write config data to a temp file and return loaded Config."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return load_config(p)


@pytest.fixture()
def config_data():
    """Return a valid minimal config dict (raw, pre-load_config)."""
    return json.loads(json.dumps(MINIMAL_CONFIG_DATA))


@pytest.fixture()
def config_file(tmp_path, config_data):
    """Write a valid config to a temp file and return the Path."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps(config_data))
    return p


@pytest.fixture()
def config(config_file):
    """Return a loaded config (output of load_config)."""
    return load_config(config_file)


@pytest.fixture(autouse=True)
def _no_fred_api_key(monkeypatch):
    """Force `_precompute_fred` to early-return in unit tests so local runs
    don't hit the real FRED API when a developer's `.env` sets FRED_API_KEY.
    Scoped to `tests/unit/` — integration/e2e layers that legitimately need a
    key are outside this conftest. Tests that want FRED data override with
    `monkeypatch.setenv(...)` + mock `fetch_fred_data`.
    """
    monkeypatch.delenv("FRED_API_KEY", raising=False)
