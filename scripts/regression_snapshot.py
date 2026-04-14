"""Snapshot timemachine.db tables for regression comparison across PR iterations."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def dump(db_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    tables = [
        ("computed_daily", "SELECT * FROM computed_daily ORDER BY date"),
        ("daily_close", "SELECT * FROM daily_close ORDER BY symbol, date"),
        ("computed_daily_tickers",
         "SELECT * FROM computed_daily_tickers ORDER BY date, ticker"),
    ]
    for name, sql in tables:
        with (out_dir / f"{name}.csv").open("w", encoding="utf-8") as f:
            for row in conn.execute(sql):
                f.write(",".join(str(v) for v in row) + "\n")
    conn.close()
    print(f"snapshot -> {out_dir}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: regression_snapshot.py <db_path> <out_dir>", file=sys.stderr)
        sys.exit(1)
    dump(Path(sys.argv[1]), Path(sys.argv[2]))
