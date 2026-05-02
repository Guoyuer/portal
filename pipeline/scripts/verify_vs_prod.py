"""Pre-sync gate: guard against local data loss and historical value drift.

Exit codes:
    0 — pass; sync is safe to proceed
    1 — drift detected; sync would silently rewrite prod history (STOP)
    2 — infrastructure error (wrangler auth/network/CLI crash); the gate
        couldn't reach prod, so drift status is unknown. Retry once env
        is healthy. Mapped by the orchestrator to EXIT_PARITY_INFRA.

The daily incremental flow ALWAYS produces local > prod (Yahoo fetches new
prices before sync), so an "exact match" gate would block every automated run.
Instead, this gate checks the only two things that actually matter:

    1. Local data loss / partial rebuild — local MUST NOT have FEWER rows
       than prod for any tracked table. If it does (e.g. DB deleted and
       rebuilt from a subset of CSVs), sync would range-replace with the
       subset and prod would lose data. FAIL.

    2. Historical value drift — rows present in BOTH local and prod with
       different values for immutable windows:
         - daily_close: rows before the shared refresh window must match to
           4 decimals (recent prices can be legitimately re-fetched)
         - computed_daily: the full range that sync will replace is compared;
           drift is allowed only inside the shared refresh window

What this gate intentionally IGNORES:
    - local > prod on row counts (the pre-sync normal; sync closes the gap)
    - rows only in local, not prod (sync will propagate them)
    - recent daily_close / computed_daily value differences inside the
      refresh window

Samples (by default):
    - 10 random (symbol, date) rows from daily_close → compare `close`
      (rows from the refresh window are filtered out before compare)
    - All computed_daily.total rows in the sync replacement range → compare
      within $1 where both sides have the date
    - Row counts for every synced table, scoped to the rows that sync can
      delete for range-replace tables

Requires: wrangler CLI authenticated, running from anywhere (uses worker dir).

Usage:
    python scripts/verify_vs_prod.py                    # full check
    python scripts/verify_vs_prod.py --sample-size 20   # more samples
    python scripts/verify_vs_prod.py --verbose          # show all comparisons
"""
from __future__ import annotations

# ruff: noqa: E402, I001

import argparse
import json
import os
import random
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

# ── Paths ─────────────────────────────────────────────────────────────────

_PROJECT_DIR = Path(__file__).resolve().parent.parent

# Make etl/ importable and load pipeline/.env before any os.environ lookups.
sys.path.insert(0, str(_PROJECT_DIR))
import etl.dotenv_loader  # noqa: F401  (side effect: load pipeline/.env)
from etl.prices import refresh_window_start
from scripts._wrangler import run_wrangler_query, sql_escape
from scripts.sync_policy import (
    DIFF_TABLES as _DIFF_TABLES,
    RANGE_TABLES as _RANGE_TABLES,
    TABLES_TO_SYNC,
    auto_derive_since,
    sync_mode_for_table,
)

_DB_PATH = Path(os.environ.get("PORTAL_DB_PATH", str(_PROJECT_DIR / "data" / "timemachine.db")))

_CLOSE_TOLERANCE = 0.0001
_TOTAL_TOLERANCE_DOLLARS = 1.0

# Exit codes used by the gate itself. The orchestrator (etl/automation/runner.py)
# translates these into the email-level EXIT_PARITY_FAIL / EXIT_PARITY_INFRA
# so the operator can tell "data drift" from "couldn't reach prod" at a glance.
_DRIFT_EXIT_CODE = 1
_INFRA_EXIT_CODE = 2


# ── Types ─────────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    ok: bool
    table: str
    detail: str


# ── Wrangler wrapper ──────────────────────────────────────────────────────

def parse_wrangler_json(raw: str) -> list[dict[str, Any]]:
    """Extract rows from wrangler d1 execute --json output.

    Exposed for tests that exercise the JSON-unwrap logic directly without
    hitting subprocess; production calls go through
    :func:`scripts._wrangler.run_wrangler_query` (which unwraps internally).
    """
    data = json.loads(raw)
    if isinstance(data, list) and data and "results" in data[0]:
        return data[0]["results"]
    return []


# ── Comparisons ────────────────────────────────────────────────────────────

def compare_row_counts(
    table: str,
    local: int,
    prod: int,
    expected_drop: int = 0,
    *,
    allow_local_short: bool | None = None,
    scope: str = "",
) -> CheckResult:
    """OK when local >= prod, OR when the shortfall matches the operator's
    declared ``expected_drop``, OR when the table's sync mode is DIFF
    (INSERT OR IGNORE preserves prod extras — no data loss possible).
    FAIL only when local is unexpectedly SHORT for a table whose sync
    would actually delete prod rows.
    """
    short_ok = (table in _DIFF_TABLES) if allow_local_short is None else allow_local_short
    prefix = f"{scope}: " if scope else ""
    if local == prod:
        return CheckResult(ok=True, table=table, detail=f"{prefix}{local} rows (match)")
    if local > prod:
        return CheckResult(
            ok=True, table=table,
            detail=f"{prefix}local={local} prod={prod} (local ahead by {local - prod} — will sync)",
        )
    shortfall = prod - local
    if expected_drop and shortfall == expected_drop:
        return CheckResult(
            ok=True, table=table,
            detail=f"{prefix}local={local} prod={prod} (short by {shortfall} — declared via --expected-drops)",
        )
    if short_ok:
        # DIFF sync = INSERT OR IGNORE. Prod extras are preserved.
        return CheckResult(
            ok=True, table=table,
            detail=f"{prefix}local={local} prod={prod} (short by {shortfall} — {table} sync is INSERT OR IGNORE, prod extras preserved)",
        )
    return CheckResult(
        ok=False, table=table,
        detail=f"{prefix}local={local} prod={prod} (local SHORT by {shortfall} — DATA LOSS RISK)",
    )


def compare_daily_close_samples(
    local: list[dict[str, Any]],
    prod: list[dict[str, Any]],
    tolerance: float = _CLOSE_TOLERANCE,
    today: date | None = None,
) -> list[CheckResult]:
    """Compare rows before the shared refresh window only.

    Recent prices can legitimately be re-fetched and differ, so they are
    excluded from the comparison. Rows present only in local are ignored
    (sync will propagate them). If every sampled row is within the recent
    window, return a single informational OK result (don't fail the gate).
    """
    cutoff = refresh_window_start(today or date.today()).isoformat()
    historical = [r for r in local if r["date"] < cutoff]
    if not historical:
        return [CheckResult(
            ok=True, table="daily_close",
            detail=f"sample was all within refresh window (>= {cutoff}) — skipped",
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
    today: date | None = None,
) -> list[CheckResult]:
    """Compare computed_daily rows present in BOTH sides.

    Callers pass the rows from the sync replacement range. Drift inside the
    shared refresh window is expected; older drift is a logic/data regression
    that sync would rewrite into prod.

    Rows present only in local are sync lag and are OK. Rows present only in
    prod are caught by the scoped row-count check for ``computed_daily``.
    """
    cutoff = refresh_window_start(today or date.today()).isoformat()
    prod_map = {r["date"]: float(r["total"]) for r in prod}
    results: list[CheckResult] = []
    for r in local:
        d = r["date"]
        lv = float(r["total"])
        pv = prod_map.get(d)
        if pv is None:
            results.append(CheckResult(
                ok=True, table="computed_daily",
                detail=f"{d} only in local (will sync)",
            ))
            continue
        diff = lv - pv
        within_tol = abs(diff) <= tolerance_dollars
        if within_tol:
            results.append(CheckResult(ok=True, table="computed_daily", detail=f"{d} within ${tolerance_dollars}"))
        elif d >= cutoff:
            # Refresh window — drift is expected; range-replace sync resolves it.
            results.append(CheckResult(
                ok=True, table="computed_daily",
                detail=f"{d} local={lv:.2f} prod={pv:.2f} diff={diff:+.2f} (refresh window — will sync)",
            ))
        else:
            results.append(CheckResult(
                ok=False, table="computed_daily",
                detail=f"{d} local={lv:.2f} prod={pv:.2f} diff={diff:+.2f} (historical — immutable)",
            ))
    return results


# ── Main ──────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Parity check: local timemachine.db vs prod D1")
    p.add_argument("--sample-size", type=int, default=10, help="Random daily_close samples (default 10)")
    p.add_argument("--verbose", action="store_true", help="Print all comparisons, not just mismatches")
    p.add_argument(
        "--expected-drops",
        action="append", default=[],
        metavar="TABLE=N",
        help=(
            "Declare an intentional row-count drop for TABLE. N is the exact "
            "number of rows local should be SHORT by vs prod; the check "
            "passes when the shortfall matches. Repeat for each table. "
            "Example: --expected-drops qianji_transactions=11 (drops the "
            "11 balance-adjustment rows filtered out at ingest)."
        ),
    )
    return p.parse_args()


def _parse_expected_drops(specs: list[str]) -> dict[str, int]:
    """Parse ``TABLE=N`` flags into ``{table: N}``."""
    out: dict[str, int] = {}
    for spec in specs:
        if "=" not in spec:
            raise SystemExit(f"--expected-drops expects TABLE=N, got: {spec!r}")
        k, v = spec.split("=", 1)
        try:
            out[k.strip()] = int(v)
        except ValueError as e:
            raise SystemExit(f"--expected-drops {spec!r}: N must be an integer") from e
    return out


def _count_local(conn: sqlite3.Connection, table: str, *, date_expr: str | None = None, since: str | None = None) -> int:
    if date_expr is None:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
    return int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {date_expr} > ?", (since,)).fetchone()[0])  # noqa: S608


def _count_prod(table: str, *, date_expr: str | None = None, since: str | None = None) -> int:
    if date_expr is None:
        sql = f"SELECT COUNT(*) AS n FROM {table}"  # noqa: S608
    else:
        sql = f"SELECT COUNT(*) AS n FROM {table} WHERE {date_expr} > {sql_escape(since)}"  # noqa: S608
    rows = run_wrangler_query(sql)
    return int(rows[0]["n"]) if rows else 0


def main() -> None:
    args = _parse_args()
    expected_drops = _parse_expected_drops(args.expected_drops)

    if not _DB_PATH.exists():
        print(f"Error: local DB not found: {_DB_PATH}", file=sys.stderr)
        sys.exit(_DRIFT_EXIT_CODE)

    print("=" * 60)
    print("  verify_vs_prod: local timemachine.db vs Cloudflare D1")
    print("=" * 60)
    if expected_drops:
        print(f"  Declared drops: {expected_drops}")

    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row

    all_results: list[CheckResult] = []

    try:
        since = auto_derive_since(conn)
        print(f"  Sync replacement cutoff: {since}")

        # Row counts
        print("\n[1] Row counts")
        for table in TABLES_TO_SYNC:
            table_mode = sync_mode_for_table(table)
            if table_mode == "range":
                date_expr = _RANGE_TABLES[table]
                local_n = _count_local(conn, table, date_expr=date_expr, since=since)
                prod_n = _count_prod(table, date_expr=date_expr, since=since)
                scope = f"{date_expr} > {since}"
                allow_short = False
            else:
                local_n = _count_local(conn, table)
                prod_n = _count_prod(table)
                scope = "full table"
                allow_short = table_mode == "diff"
            r = compare_row_counts(
                table, local_n, prod_n,
                expected_drop=expected_drops.get(table, 0),
                allow_local_short=allow_short,
                scope=scope,
            )
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
        if local_samples:
            conditions = " OR ".join([
                f"(symbol={sql_escape(s['symbol'])} AND date={sql_escape(s['date'])})"
                for s in local_samples
            ])
            prod_samples = run_wrangler_query(f"SELECT symbol, date, close FROM daily_close WHERE {conditions}")  # noqa: S608
        else:
            prod_samples = []
        for r in compare_daily_close_samples(local_samples, prod_samples):
            all_results.append(r)
            if args.verbose or not r.ok:
                marker = "✓" if r.ok else "✗"
                print(f"  {marker} {r.detail}")

        # computed_daily replacement range
        print("\n[3] computed_daily replacement range")
        local_totals = [dict(r) for r in conn.execute(
            "SELECT date, total FROM computed_daily WHERE date > ? ORDER BY date",
            (since,),
        ).fetchall()]
        prod_totals = run_wrangler_query(
            "SELECT date, total FROM computed_daily "
            f"WHERE date > {sql_escape(since)} ORDER BY date"
        )
        for r in compare_recent_totals(local_totals, prod_totals):
            all_results.append(r)
            if args.verbose or not r.ok:
                marker = "✓" if r.ok else "✗"
                print(f"  {marker} {r.detail}")
    except RuntimeError as e:
        # Infra failure — wrangler couldn't reach prod (auth, 5xx, CLI crash).
        # We DON'T know whether prod has drifted, so we can't return either
        # pass or drift. Exit 2 so the orchestrator can label this distinctly
        # in the email and the operator knows it's a retry-when-healthy
        # condition, not a data-investigation condition.
        conn.close()
        print("\n" + "=" * 60, file=sys.stderr)
        print(f"  INFRA FAIL: wrangler unreachable\n  {e}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        sys.exit(_INFRA_EXIT_CODE)

    conn.close()

    # Summary
    failed = [r for r in all_results if not r.ok]
    print("\n" + "=" * 60)
    if failed:
        print(f"  FAIL: {len(failed)} mismatches")
        for r in failed:
            print(f"    - {r.table}: {r.detail}")
        print("=" * 60)
        sys.exit(_DRIFT_EXIT_CODE)
    print(f"  PASS: {len(all_results)} checks, all within tolerance")
    print("=" * 60)


if __name__ == "__main__":
    main()
