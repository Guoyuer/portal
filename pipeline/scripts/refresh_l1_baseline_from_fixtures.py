"""Refresh L1 regression baselines from the committed L2 fixtures.

Builds ``timemachine.db`` against ``tests/fixtures/regression/`` (the same
inputs the L2 golden test uses) and writes the canonical row-level hashes
into ``tests/regression/baseline/*.sha256``.

The output is deterministic — same fixtures in, same hashes out — so CI
can re-run this against a PR branch and commit the result back.

Run from ``pipeline/``::

    python scripts/refresh_l1_baseline_from_fixtures.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
FIXTURE_DIR = PIPELINE_DIR / "tests" / "fixtures" / "regression"
BASELINE_DIR = PIPELINE_DIR / "tests" / "regression" / "baseline"

# Mirror the L2 test — keep in lockstep with ``test_pipeline_golden.py``.
DOWNLOAD_FIXTURES = [
    "Accounts_History_fixture.csv",
    "Bloomberg.Download_fixture_2024-06.qfx",
    "Bloomberg.Download_fixture_2024-12.qfx",
]
ROBINHOOD_FIXTURE_SRC = "robinhood.csv"
ROBINHOOD_FIXTURE_DST = "Robinhood_history.csv"


def _build_fixture_db(work_dir: Path) -> Path:
    """Reproduce the L2 test's build steps. Returns the path to timemachine.db."""
    downloads = work_dir / "downloads"
    downloads.mkdir()

    for name in DOWNLOAD_FIXTURES:
        shutil.copy(FIXTURE_DIR / name, downloads / name)
    shutil.copy(FIXTURE_DIR / ROBINHOOD_FIXTURE_SRC, downloads / ROBINHOOD_FIXTURE_DST)

    env = os.environ.copy()
    env["QIANJI_DB_PATH_OVERRIDE"] = str(FIXTURE_DIR / "qianji.sqlite")
    env["QIANJI_CNY_RATE_OVERRIDE"] = "7.20"
    env["QIANJI_USER_TZ"] = "UTC"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_timemachine_db.py",
            "--data-dir", str(work_dir),
            "--config", str(FIXTURE_DIR / "config.json"),
            "--downloads", str(downloads),
            "--prices-from-csv", str(FIXTURE_DIR / "prices.csv"),
            "--dry-run-market",
            "--no-validate",
            "--as-of", "2026-04-14",
        ],
        cwd=str(PIPELINE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        sys.stderr.write(
            f"build failed (rc={result.returncode})\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}\n"
        )
        sys.exit(result.returncode)
    return work_dir / "timemachine.db"


def main() -> int:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="l1-baseline-") as tmp:
        db_path = _build_fixture_db(Path(tmp))
        # Delegate to the canonical hasher so L1 + L2 + this refresher all
        # agree on the serialization.
        result = subprocess.run(
            [
                sys.executable,
                str(PIPELINE_DIR / "scripts" / "_regression_util.py"),
                "hash",
                str(db_path),
                str(BASELINE_DIR),
            ],
            cwd=str(PIPELINE_DIR),
            capture_output=False,
        )
        return result.returncode


if __name__ == "__main__":
    sys.exit(main())
