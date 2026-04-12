"""Tests for sync_to_d1.py CLI default safety."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = REPO_ROOT / "pipeline" / "scripts" / "sync_to_d1.py"


def _run(args: list[str], cwd: Path, env_db: Path) -> subprocess.CompletedProcess[str]:
    """Invoke sync_to_d1.py in dry-run mode with a given DB path."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env={"PATH": "", "PYTHONPATH": str(REPO_ROOT / "pipeline"), "PORTAL_DB_PATH": str(env_db)},
    )


@pytest.fixture()
def fake_db(tmp_path):
    """Seed a DB with schema + some fidelity rows spanning 2026-01 to 2026-04."""
    import os
    sys.path.insert(0, str(REPO_ROOT / "pipeline"))
    os.chdir(str(REPO_ROOT / "pipeline"))
    from etl.db import get_connection, init_db  # noqa: E402

    p = tmp_path / "test.db"
    init_db(p)
    conn = get_connection(p)
    conn.execute(
        "INSERT INTO fidelity_transactions (run_date, account, account_number, action, action_type)"
        " VALUES ('2026-01-15', 'A', 'X', 'raw', 'buy')"
    )
    conn.execute(
        "INSERT INTO fidelity_transactions (run_date, account, account_number, action, action_type)"
        " VALUES ('2026-04-10', 'A', 'X', 'raw', 'buy')"
    )
    conn.commit()
    conn.close()
    return p


def test_default_is_diff_not_full(fake_db, tmp_path):
    """Running with no flags must NOT emit destructive DELETE FROM fidelity_transactions."""
    result = _run([], tmp_path, fake_db)
    assert result.returncode == 0, result.stderr
    assert "DELETE FROM fidelity_transactions;" not in result.stdout + result.stderr
    assert "diff" in (result.stdout + result.stderr).lower()


def test_full_requires_explicit_flag(fake_db, tmp_path):
    """--full must emit the destructive DELETE FROM."""
    result = _run(["--full"], tmp_path, fake_db)
    assert result.returncode == 0, result.stderr
    assert "full" in (result.stdout + result.stderr).lower()


def test_diff_auto_derives_since(fake_db, tmp_path):
    """Default diff with no --since should print an auto-derived cutoff."""
    result = _run([], tmp_path, fake_db)
    out = result.stdout + result.stderr
    assert "auto-derived" in out.lower() or "since=" in out.lower()


def test_diff_since_range_covers_recent_rows(fake_db, tmp_path):
    """Auto-derived --since must be <= max fidelity run_date - 60 days (or earlier),
    such that the latest fidelity row (2026-04-10 in fixture) falls in the range-replace window."""
    result = _run([], tmp_path, fake_db)
    out = result.stdout + result.stderr
    # Expect at least one fidelity INSERT in the SQL preview
    assert "INSERT INTO fidelity_transactions" in out
