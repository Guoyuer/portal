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


@pytest.fixture
def qianji_sample_csv():
    return FIXTURES_DIR / "qianji_sample.csv"


@pytest.fixture
def positions_sample_csv():
    """Latest real positions CSV if available."""
    csvs = sorted(Path("data").glob("Portfolio_Positions_*.csv"))
    if not csvs:
        pytest.skip("No positions CSV found in data/")
    return csvs[-1]
