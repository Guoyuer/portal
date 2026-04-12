"""Tests for etl.dotenv_loader — pipeline/.env auto-loader.

Contract:
    - Loads KEY=VALUE pairs from pipeline/.env into os.environ on import.
    - override=False: pre-existing env vars always win over .env entries
      (so setx-persisted User vars in Windows Task Scheduler still take priority).
    - Missing .env file is a silent no-op (no exception).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import etl.dotenv_loader as dotenv_loader


@pytest.fixture()
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip test env keys between cases so each test starts from a known state."""
    for key in ("PORTAL_TEST_FOO", "PORTAL_TEST_BAR"):
        monkeypatch.delenv(key, raising=False)


def test_load_reads_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _clean_env: None
) -> None:
    """A KEY=VALUE line in .env should populate os.environ on _load()."""
    env_file = tmp_path / ".env"
    env_file.write_text("PORTAL_TEST_FOO=bar\n", encoding="utf-8")
    monkeypatch.setattr(dotenv_loader, "_ENV_PATH", env_file)

    dotenv_loader._load()

    assert os.environ.get("PORTAL_TEST_FOO") == "bar"


def test_existing_env_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _clean_env: None
) -> None:
    """Pre-existing env vars must take precedence over .env entries (override=False)."""
    monkeypatch.setenv("PORTAL_TEST_FOO", "already")
    env_file = tmp_path / ".env"
    env_file.write_text("PORTAL_TEST_FOO=from-dotenv\n", encoding="utf-8")
    monkeypatch.setattr(dotenv_loader, "_ENV_PATH", env_file)

    dotenv_loader._load()

    assert os.environ.get("PORTAL_TEST_FOO") == "already"


def test_missing_env_file_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _clean_env: None
) -> None:
    """Nonexistent .env path should load cleanly without raising."""
    missing = tmp_path / "definitely-not-here.env"
    assert not missing.exists()
    monkeypatch.setattr(dotenv_loader, "_ENV_PATH", missing)

    dotenv_loader._load()  # should not raise

    assert os.environ.get("PORTAL_TEST_FOO") is None


def test_module_imported_at_script_start() -> None:
    """Entry-point scripts must import etl.dotenv_loader so .env is loaded early."""
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    entries = [
        "run_automation.py",
        "build_timemachine_db.py",
        "sync_to_d1.py",
        "verify_vs_prod.py",
        "verify_positions.py",
    ]
    for name in entries:
        source = (scripts_dir / name).read_text(encoding="utf-8")
        assert "import etl.dotenv_loader" in source, (
            f"{name} must import etl.dotenv_loader to auto-load pipeline/.env"
        )
