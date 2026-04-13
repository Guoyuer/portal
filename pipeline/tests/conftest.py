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
