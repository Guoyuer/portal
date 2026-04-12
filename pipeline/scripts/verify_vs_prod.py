"""Pre-sync gate: guard against local data loss and historical value drift.

Exits 0 when sync is safe. Exits 1 on any real failure (STOP, investigate).

The daily incremental flow ALWAYS produces local > prod (Yahoo fetches new
prices before sync), so an "exact match" gate would block every automated run.
Instead, this gate checks the only two things that actually matter:

    1. Local data loss / partial rebuild — local MUST NOT have FEWER rows
       than prod for any tracked table. If it does (e.g. DB deleted and
       rebuilt from a subset of CSVs), sync would range-replace with the
       subset and prod would lose data. FAIL.

    2. Historical value drift — rows present in BOTH local and prod with
       different values for immutable windows:
         - daily_close: rows with date <= today - 7 must match to 4 decimals
           (recent prices can be legitimately re-fetched; newer-than-7-day
           drift is normal)
         - computed_daily: recent 7 days present in both sides must agree
           within $1 (this table is INSERT OR IGNORE so prod values are
           frozen; drift implies a logic change that would desync)

What this gate intentionally IGNORES:
    - local > prod on row counts (the pre-sync normal; sync closes the gap)
    - rows only in local, not prod (sync will propagate them)
    - recent (< 7 days) daily_close value differences

Samples (by default):
    - 10 random (symbol, date) rows from daily_close → compare `close`
      (rows from the recent 7-day window are filtered out before compare)
    - Last 7 days of computed_daily.total → compare within $1 where both
      sides have the date
    - Row counts for 4 core tables (direction check: local >= prod)

Requires: wrangler CLI authenticated, running from anywhere (uses worker dir).

Usage:
    python scripts/verify_vs_prod.py                    # full check
    python scripts/verify_vs_prod.py --sample-size 20   # more samples
    python scripts/verify_vs_prod.py --verbose          # show all comparisons
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# ── Paths ─────────────────────────────────────────────────────────────────

_PROJECT_DIR = Path(__file__).resolve().parent.parent
_DB_PATH = Path(os.environ.get("PORTAL_DB_PATH", str(_PROJECT_DIR / "data" / "timemachine.db")))
_WORKER_DIR = _PROJECT_DIR.parent / "worker"

_TABLES_FOR_COUNT = ["fidelity_transactions", "qianji_transactions", "computed_daily", "daily_close"]
_CLOSE_TOLERANCE = 0.0001
_TOTAL_TOLERANCE_DOLLARS = 1.0


# ── Types ─────────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    ok: bool
    table: str
    detail: str


# ── Wrangler wrapper ──────────────────────────────────────────────────────

def parse_wrangler_json(raw: str) -> list[dict[str, Any]]:
    """Extract rows from wrangler d1 execute --json output."""
    data = json.loads(raw)
    if isinstance(data, list) and data and "results" in data[0]:
        return data[0]["results"]
    return []


def _query_prod(sql: str) -> list[dict[str, Any]]:
    """Run a SELECT against prod D1, return rows. Raises on failure."""
    cmd = f'npx wrangler d1 execute portal-db --remote --json --command="{sql}"'
    result = subprocess.run(cmd, cwd=str(_WORKER_DIR), capture_output=True, text=True, shell=True)
    if result.returncode != 0:
        raise RuntimeError(f"wrangler failed:\n{result.stderr}")
    return parse_wrangler_json(result.stdout)


# ── Comparisons ────────────────────────────────────────────────────────────

def compare_row_counts(table: str, local: int, prod: int) -> CheckResult:
    """OK when local >= prod. FAIL only when local is SHORT (data loss risk)."""
    if local == prod:
        return CheckResult(ok=True, table=table, detail=f"{local} rows (match)")
    if local > prod:
        return CheckResult(
            ok=True, table=table,
            detail=f"local={local} prod={prod} (local ahead by {local - prod} — will sync)",
        )
    return CheckResult(
        ok=False, table=table,
        detail=f"local={local} prod={prod} (local SHORT by {prod - local} — DATA LOSS RISK)",
    )


def compare_daily_close_samples(
    local: list[dict[str, Any]],
    prod: list[dict[str, Any]],
    tolerance: float = _CLOSE_TOLERANCE,
    today: date | None = None,
) -> list[CheckResult]:
    """Compare historical (date <= today - 7) rows only.

    Recent prices can legitimately be re-fetched and differ, so they are
    excluded from the comparison. Rows present only in local are ignored
    (sync will propagate them). If every sampled row is within the recent
    window, return a single informational OK result (don't fail the gate).
    """
    cutoff = ((today or date.today()) - timedelta(days=7)).isoformat()
    historical = [r for r in local if r["date"] <= cutoff]
    if not historical:
        return [CheckResult(
            ok=True, table="daily_close",
            detail=f"sample was all within recent window (> {cutoff}) — skipped",
        )]

    prod_map = {(r["symbol"], r["date"]): r["close"] for r in prod}
    results: list[CheckResult] = []
    for r in historical:
        key = (r["symbol"], r["date"])
        lv = float(r["close"])
        pv = prod_map.get(key)
        if pv is None:
            # Historical row missing in prod is unusual but not a drift failure.
            # Sync will insert it; real data loss would be caught by row-count check.
            results.append(CheckResult(
                ok=True, table="daily_close",
                detail=f"{key} only in local (will sync)",
            ))
            continue
        if abs(lv - float(pv)) > tolerance:
            results.append(CheckResult(ok=False, table="daily_close", detail=f"{key} local={lv} prod={pv}"))
        else:
            results.append(CheckResult(ok=True, table="daily_close", detail=f"{key} match"))
    return results


def compare_recent_totals(
    local: list[dict[str, Any]],
    prod: list[dict[str, Any]],
    tolerance_dollars: float = _TOTAL_TOLERANCE_DOLLARS,
) -> list[CheckResult]:
    """Compare only dates present in BOTH sides.

    Dates only in local are skipped (sync will propagate them — normal).
    `computed_daily` is INSERT OR IGNORE, so prod rows are frozen; any
    value drift for a shared date implies a logic change that would
    desync downstream consumers. FAIL in that case.
    """
    prod_map = {r["date"]: float(r["total"]) for r in prod}
    results: list[CheckResult] = []
    for r in local:
        d = r["date"]
        lv = float(r["total"])
        pv = prod_map.get(d)
        if pv is None:
            # Only in local — sync will insert. Not a drift failure.
            results.append(CheckResult(
                ok=True, table="computed_daily",
                detail=f"{d} only in local (will sync)",
            ))
            continue
        if abs(lv - pv) > tolerance_dollars:
            results.append(CheckResult(ok=False, table="computed_daily",
                                        detail=f"{d} local={lv:.2f} prod={pv:.2f} diff={lv - pv:+.2f}"))
        else:
            results.append(CheckResult(ok=True, table="computed_daily", detail=f"{d} within ${tolerance_dollars}"))
    return results


# ── Main ──────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Parity check: local timemachine.db vs prod D1")
    p.add_argument("--sample-size", type=int, default=10, help="Random daily_close samples (default 10)")
    p.add_argument("--verbose", action="store_true", help="Print all comparisons, not just mismatches")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    if not _DB_PATH.exists():
        print(f"Error: local DB not found: {_DB_PATH}", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("  verify_vs_prod: local timemachine.db vs Cloudflare D1")
    print("=" * 60)

    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row

    all_results: list[CheckResult] = []

    # Row counts
    print("\n[1] Row counts")
    for table in _TABLES_FOR_COUNT:
        local_n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        prod_rows = _query_prod(f"SELECT COUNT(*) AS n FROM {table}")
        prod_n = int(prod_rows[0]["n"]) if prod_rows else 0
        r = compare_row_counts(table, local_n, prod_n)
        all_results.append(r)
        marker = "✓" if r.ok else "✗"
        print(f"  {marker} {table}: {r.detail}")

    # daily_close random sample
    print(f"\n[2] daily_close sample ({args.sample_size} random rows)")
    all_pairs = conn.execute("SELECT symbol, date FROM daily_close").fetchall()
    random.seed(42)
    sampled = random.sample(list(all_pairs), min(args.sample_size, len(all_pairs)))
    local_samples = []
    for sym, d in sampled:
        row = conn.execute("SELECT symbol, date, close FROM daily_close WHERE symbol=? AND date=?",
                           (sym, d)).fetchone()
        local_samples.append(dict(row))
    # Batch query prod
    conditions = " OR ".join([f"(symbol='{s['symbol']}' AND date='{s['date']}')" for s in local_samples])
    prod_samples = _query_prod(f"SELECT symbol, date, close FROM daily_close WHERE {conditions}")
    for r in compare_daily_close_samples(local_samples, prod_samples):
        all_results.append(r)
        if args.verbose or not r.ok:
            marker = "✓" if r.ok else "✗"
            print(f"  {marker} {r.detail}")

    # Recent totals
    print("\n[3] computed_daily recent 7 days")
    local_totals = [dict(r) for r in conn.execute(
        "SELECT date, total FROM computed_daily ORDER BY date DESC LIMIT 7"
    ).fetchall()]
    prod_totals = _query_prod(
        "SELECT date, total FROM computed_daily ORDER BY date DESC LIMIT 7"
    )
    for r in compare_recent_totals(local_totals, prod_totals):
        all_results.append(r)
        if args.verbose or not r.ok:
            marker = "✓" if r.ok else "✗"
            print(f"  {marker} {r.detail}")

    conn.close()

    # Summary
    failed = [r for r in all_results if not r.ok]
    print("\n" + "=" * 60)
    if failed:
        print(f"  FAIL: {len(failed)} mismatches")
        for r in failed:
            print(f"    - {r.table}: {r.detail}")
        print("=" * 60)
        sys.exit(1)
    print(f"  PASS: {len(all_results)} checks, all within tolerance")
    print("=" * 60)


if __name__ == "__main__":
    main()
