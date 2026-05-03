"""L2 regression: build timemachine.db from committed synthetic fixtures
and assert that ``computed_daily`` + ``computed_daily_tickers`` match the
committed golden JSON.

Runs offline (no Yahoo fetches, no network, no wrangler) — the build
reads prices from a committed CSV via ``--prices-from-csv``, which also
skips market-index precompute. The Qianji DB is
swapped in via the ``QIANJI_DB_PATH_OVERRIDE`` env var so the module-
level default path never reaches the caller's home directory.

Designed for CI: only inputs are files in ``tests/fixtures/regression/``
and the build script itself.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PIPELINE_DIR = Path(__file__).resolve().parent.parent.parent
FIXTURE_DIR = PIPELINE_DIR / "tests" / "fixtures" / "regression"
GOLDEN = FIXTURE_DIR / "golden.json"

# Files the production Fidelity/Empower globs look for. These names are
# committed under FIXTURE_DIR and must be copied into the build's
# ``<data-dir>/downloads/`` so the globs pick them up.
DOWNLOAD_FIXTURES = [
    "Accounts_History_fixture.csv",
    "Bloomberg.Download_fixture_2024-06.qfx",
    "Bloomberg.Download_fixture_2024-12.qfx",
]

# Robinhood now globs ``Robinhood_history*.csv`` in the downloads directory,
# same as Fidelity. The fixture still ships under ``robinhood.csv`` for
# clarity and is renamed on copy to match the production glob.
ROBINHOOD_FIXTURE_SRC = "robinhood.csv"
ROBINHOOD_FIXTURE_DST = "Robinhood_history.csv"
EXCLUDED_COLUMNS = {
    "computed_daily": frozenset({"created_at", "updated_at"}),
    "computed_daily_tickers": frozenset({"created_at", "updated_at"}),
}


def _resolve_python() -> str:
    """Prefer the pinned venv interpreter so the test doesn't depend on the
    user's active venv. Falls back to ``sys.executable`` in CI or other
    environments where the Windows venv path doesn't exist.
    """
    venv_py = PIPELINE_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


@pytest.fixture(scope="module")
def built_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build timemachine.db against the L2 fixture inputs.

    Copies the Fidelity / Empower / Robinhood fixtures into a scratch
    ``downloads/`` directory so production globs pick them up, points the
    Qianji DB loader at the fixture SQLite via the override env var, and
    invokes ``build_timemachine_db.py`` with ``--prices-from-csv`` so the
    run is fully offline.
    """
    data_dir = tmp_path_factory.mktemp("regression")
    downloads = data_dir / "downloads"
    downloads.mkdir()

    for name in DOWNLOAD_FIXTURES:
        shutil.copy(FIXTURE_DIR / name, downloads / name)
    shutil.copy(FIXTURE_DIR / ROBINHOOD_FIXTURE_SRC, downloads / ROBINHOOD_FIXTURE_DST)

    env = os.environ.copy()
    env["QIANJI_DB_PATH_OVERRIDE"] = str(FIXTURE_DIR / "qianji.sqlite")
    # Offline: pin CNY rate + user timezone so the build never touches
    # Yahoo and dates land deterministically regardless of CI host tz.
    env["QIANJI_CNY_RATE_OVERRIDE"] = "7.20"
    env["QIANJI_USER_TZ"] = "UTC"

    python = _resolve_python()
    result = subprocess.run(
        [
            python,
            "scripts/build_timemachine_db.py",
            "--data-dir", str(data_dir),
            "--config", str(FIXTURE_DIR / "config.json"),
            "--downloads", str(downloads),
            "--prices-from-csv", str(FIXTURE_DIR / "prices.csv"),
            "--no-validate",
            "--as-of", "2026-04-14",
        ],
        cwd=str(PIPELINE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"build failed (rc={result.returncode})\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return data_dir / "timemachine.db"


def _load_table(db_path: Path, table: str) -> list[dict[str, object]]:
    excluded = EXCLUDED_COLUMNS.get(table, frozenset())
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cols_meta = conn.execute(f"PRAGMA table_info({table})").fetchall()
        cols = [c["name"] for c in cols_meta if c["name"] not in excluded]
        pk_cols = [c["name"] for c in cols_meta if c["pk"] > 0] or cols
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} ORDER BY {', '.join(pk_cols)}"  # noqa: S608
        ).fetchall()
    finally:
        conn.close()
    return [
        {c: (repr(row[c]) if isinstance(row[c], float) else row[c]) for c in cols}
        for row in rows
    ]


def test_computed_daily_matches_golden(built_db: Path) -> None:
    """L2: the committed fixtures + committed golden must stay in lockstep."""
    assert GOLDEN.exists(), (
        f"golden not committed at {GOLDEN}. Regenerate with scripts/regenerate_l2_golden.py"
    )
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    actual = {
        "computed_daily": _load_table(built_db, "computed_daily"),
        "computed_daily_tickers": _load_table(built_db, "computed_daily_tickers"),
    }
    assert actual == golden, "L2 regression: computed tables diverged from golden"
