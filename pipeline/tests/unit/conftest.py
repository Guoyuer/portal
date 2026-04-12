"""Shared fixtures for unit tests."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import pytest

from etl.config import load_config
from etl.types import Config, Portfolio

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


def write_csv(
    tmp_path: Path, rows: list[dict[str, str]], filename: str = "Portfolio_Positions_Jan-01-2026.csv"
) -> Path:
    """Write a CSV with the standard Fidelity headers."""
    p = tmp_path / filename
    fieldnames = ["Symbol", "Description", "Current Value"]
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    return p


def load_test_config(tmp_path: Path, data: dict) -> Config:
    """Write config data to a temp file and return loaded Config."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return load_config(p)


def make_portfolio(totals_map: dict[str, float]) -> Portfolio:
    """Build a portfolio dict from {ticker: value}."""
    totals = defaultdict(float, totals_map)
    counts = defaultdict(int, {t: 1 for t in totals_map})
    return Portfolio(
        totals=totals,
        counts=counts,
        total=sum(totals.values()),
        cost_basis=defaultdict(float),
        gain_loss=defaultdict(float),
        gain_loss_pct={},
    )


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


@pytest.fixture()
def simple_csv(tmp_path):
    """A CSV with one row per asset in MINIMAL_CONFIG_DATA."""
    rows = [
        {"Symbol": "VOO", "Description": "Vanguard S&P 500 ETF", "Current Value": "$55,000.00"},
        {"Symbol": "QQQM", "Description": "Invesco NASDAQ 100", "Current Value": "$10,000.00"},
        {"Symbol": "VXUS", "Description": "Vanguard Total Intl", "Current Value": "$15,000.00"},
        {"Symbol": "FBTC", "Description": "Fidelity Bitcoin", "Current Value": "$5,000.00"},
        {"Symbol": "SGOV", "Description": "iShares 0-3 Month", "Current Value": "$10,000.00"},
        {"Symbol": "VGLT", "Description": "Vanguard Long-Term", "Current Value": "$5,000.00"},
    ]
    return write_csv(tmp_path, rows)
