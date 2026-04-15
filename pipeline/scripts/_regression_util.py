"""Canonical JSON dump + SHA256 for regression hashes.
Row-level: every computed_daily / computed_daily_tickers row is serialized in PK order
with stable key ordering and full-precision float strings. Noise columns excluded."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

# Columns excluded from hashing (timestamps, autoincrement ids not part of logical identity)
EXCLUDED_COLUMNS: dict[str, frozenset[str]] = {
    "computed_daily": frozenset({"created_at", "updated_at"}),
    "computed_daily_tickers": frozenset({"created_at", "updated_at"}),
}

TABLES = ["computed_daily", "computed_daily_tickers"]


def dump_canonical(db_path: Path, table: str) -> str:
    excluded = EXCLUDED_COLUMNS.get(table, frozenset())
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cols_meta = conn.execute(f"PRAGMA table_info({table})").fetchall()
    cols = [c["name"] for c in cols_meta if c["name"] not in excluded]
    pk_cols = [c["name"] for c in cols_meta if c["pk"] > 0] or cols
    order_by = ", ".join(pk_cols)
    rows = conn.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {order_by}").fetchall()
    conn.close()
    # Canonical serialization: sort keys, preserve float precision via repr
    out = [{c: (repr(r[c]) if isinstance(r[c], float) else r[c]) for c in cols} for r in rows]
    return json.dumps(out, sort_keys=True, separators=(",", ":"))


def sha256_of(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def main() -> int:
    mode = sys.argv[1]  # "dump" | "hash" | "compare"
    db_path = Path(sys.argv[2])
    out_dir = Path(sys.argv[3])
    out_dir.mkdir(parents=True, exist_ok=True)
    rc = 0
    for table in TABLES:
        body = dump_canonical(db_path, table)
        (out_dir / f"{table}.json").write_text(body, encoding="utf-8")
        digest = sha256_of(body)
        hash_file = out_dir / f"{table}.sha256"
        if mode == "dump" or mode == "hash":
            hash_file.write_text(digest + "\n", encoding="utf-8")
            print(f"{table}: {digest}")
        elif mode == "compare":
            expected = hash_file.read_text(encoding="utf-8").strip()
            if expected != digest:
                print(f"REGRESSION in {table}: expected {expected}, got {digest}", file=sys.stderr)
                rc = 1
            else:
                print(f"{table}: OK")
    return rc


if __name__ == "__main__":
    sys.exit(main())
