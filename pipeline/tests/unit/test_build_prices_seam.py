from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent.parent


def test_prices_from_csv_flag_bypasses_yahoo(tmp_path: Path) -> None:
    """With --prices-from-csv, build must not attempt a Yahoo network fetch."""
    prices_csv = tmp_path / "prices.csv"
    prices_csv.write_text("date,FXAIX\n2024-01-02,150.50\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_timemachine_db.py",
            "--prices-from-csv",
            str(prices_csv),
            "--dry-run-market",
        ],
        cwd=str(PIPELINE_DIR),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "yahoo" not in result.stderr.lower() or "skipped" in result.stderr.lower()
