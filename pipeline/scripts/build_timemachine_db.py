"""Build the timemachine SQLite database from raw data sources.

Integration script that:
  1.  Initialises data/timemachine.db with all tables
  2.  Ingests Fidelity brokerage transactions from CSV
  2b. Ingests Robinhood brokerage transactions from CSV
  3.  Ingests Empower 401k quarterly snapshots + contributions from QFX files
  4.  Fetches and stores prices + CNY rates in timemachine.db.daily_close
  5.  Computes daily allocation (reads prices from DB)
  6.  Stores results

Refreshes the last ``REFRESH_WINDOW_DAYS`` of ``computed_daily`` on every
run, plus fills any historical gap beyond the window. If the DB is missing
or empty, a full build runs automatically. To force a clean rebuild, delete
``pipeline/data/timemachine.db`` before running.

All orchestration logic lives in :mod:`etl.build`; this script is a thin
``argparse → build_timemachine_db`` entry point (mirrors the
``run_automation.py`` ↔ ``etl.automation.runner`` split) so external callers
— Task Scheduler, automation, and regression fixtures — have a stable CLI
surface.

Usage:
  python scripts/build_timemachine_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the pipeline package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import etl.dotenv_loader  # noqa: E402, F401  (side effect: load pipeline/.env)
from etl.build import _parse_args, build_timemachine_db  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return build_timemachine_db(args)


if __name__ == "__main__":
    sys.exit(main())
