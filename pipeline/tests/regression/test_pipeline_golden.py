"""L2 regression: build timemachine.db from committed synthetic fixtures.

Asserts that ``computed_daily`` + ``computed_daily_tickers`` match the
committed golden JSON. The run stays offline by monkeypatching the Yahoo
fetchers to seed prices from a fixture CSV, skipping market precompute, and
pinning Qianji's DB path/timezone in-process.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import etl.qianji.balances as qianji_balances
import etl.qianji.ingest as qianji_ingest
from etl import build as build_mod
from etl.db import get_connection

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


def _seed_fixture_prices(db_path: Path, csv_path: Path) -> None:
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        symbols = [name for name in (reader.fieldnames or []) if name != "date"]
        rows = [
            (symbol, date_iso, float(raw))
            for row in reader
            if (date_iso := (row.get("date") or "").strip())
            for symbol in symbols
            if (raw := (row.get(symbol) or "").strip())
        ]
    conn = get_connection(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_close (symbol, date, close) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def built_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build timemachine.db against the L2 fixture inputs.

    Copies the Fidelity / Empower / Robinhood fixtures into a scratch
    ``downloads/`` directory so production globs pick them up, then runs the
    production build function with external fetches replaced by fixture data.
    """
    data_dir = tmp_path / "regression"
    downloads = data_dir / "downloads"
    downloads.mkdir(parents=True)

    for name in DOWNLOAD_FIXTURES:
        shutil.copy(FIXTURE_DIR / name, downloads / name)
    shutil.copy(FIXTURE_DIR / ROBINHOOD_FIXTURE_SRC, downloads / ROBINHOOD_FIXTURE_DST)

    monkeypatch.setattr(build_mod, "DEFAULT_QJ_DB", FIXTURE_DIR / "qianji.sqlite")
    monkeypatch.setattr(qianji_ingest, "_USER_TZ", ZoneInfo("UTC"))
    monkeypatch.setattr(qianji_balances, "_USER_TZ", ZoneInfo("UTC"))

    def fake_prices(
        db_path: Path,
        _periods: dict[str, tuple[date, date | None]],
        _end: date,
        **_kwargs: date,
    ) -> None:
        _seed_fixture_prices(db_path, FIXTURE_DIR / "prices.csv")

    monkeypatch.setattr(build_mod, "fetch_and_store_prices", fake_prices)
    monkeypatch.setattr(build_mod, "fetch_and_store_cny_rates", lambda *_args: None)
    monkeypatch.setattr(build_mod, "precompute_market", lambda _db: None)

    args = argparse.Namespace(
        data_dir=data_dir,
        config=FIXTURE_DIR / "config.json",
        downloads=downloads,
        no_validate=True,
        as_of=date(2026, 4, 14),
    )
    assert build_mod.build_timemachine_db(args) == 0
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
