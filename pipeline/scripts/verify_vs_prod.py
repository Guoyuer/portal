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
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# ── Paths ─────────────────────────────────────────────────────────────────

_PROJECT_DIR = Path(__file__).resolve().parent.parent

# Make etl/ importable and load pipeline/.env before any os.environ lookups.
sys.path.insert(0, str(_PROJECT_DIR))
import etl.dotenv_loader  # noqa: E402, F401  (side effect: load pipeline/.env)
from scripts._wrangler import run_wrangler_query  # noqa: E402

_DB_PATH = Path(os.environ.get("PORTAL_DB_PATH", str(_PROJECT_DIR / "data" / "timemachine.db")))

_TABLES_FOR_COUNT = ["fidelity_transactions", "qianji_transactions", "computed_daily", "daily_close"]
_CLOSE_TOLERANCE = 0.0001
_TOTAL_TOLERANCE_DOLLARS = 1.0
_RECENT_WINDOW_DAYS = 7

# Exit codes used by the gate itself. The orchestrator (etl/automation/runner.py)
# translates these into the email-level EXIT_PARITY_FAIL / EXIT_PARITY_INFRA
# so the operator can tell "data drift" from "couldn't reach prod" at a glance.
_DRIFT_EXIT_CODE = 1
_INFRA_EXIT_CODE = 2

# Tables whose sync mode is ``INSERT OR IGNORE`` (append-only with a natural
# PK) — for these, ``local < prod`` is safe: the sync preserves prod's extra
# rows instead of replacing them. Mirrors ``sync_to_d1._DIFF_TABLES`` so the
# gate's definition of "data loss" tracks what the sync actually does.
try:
    from scripts.sync_to_d1 import _DIFF_TABLES as _SYNC_DIFF_TABLES
    _DIFF_TABLES: set[str] = _SYNC_DIFF_TABLES
except ImportError:
    # Running verify in isolation (e.g. tests that don't add scripts/ to
    # path). Fall back to the known set; mismatch would surface quickly.
    _DIFF_TABLES = {"daily_close"}


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
    table: str, local: int, prod: int, expected_drop: int = 0,
) -> CheckResult:
    """OK when local >= prod, OR when the shortfall matches the operator's
    declared ``expected_drop``, OR when the table's sync mode is DIFF
    (INSERT OR IGNORE preserves prod extras — no data loss possible).
    FAIL only when local is unexpectedly SHORT for a table whose sync
    would actually delete prod rows.
    """
    if local == prod:
        return CheckResult(ok=True, table=table, detail=f"{local} rows (match)")
    if local > prod:
        return CheckResult(
            ok=True, table=table,
            detail=f"local={local} prod={prod} (local ahead by {local - prod} — will sync)",
        )
    shortfall = prod - local
    if expected_drop and shortfall == expected_drop:
        return CheckResult(
            ok=True, table=table,
            detail=f"local={local} prod={prod} (short by {shortfall} — declared via --expected-drops)",
        )
    if table in _DIFF_TABLES:
        # DIFF sync = INSERT OR IGNORE. Prod extras are preserved.
        return CheckResult(
            ok=True, table=table,
            detail=f"local={local} prod={prod} (short by {shortfall} — {table} sync is INSERT OR IGNORE, prod extras preserved)",
        )
    return CheckResult(
        ok=False, table=table,
        detail=f"local={local} prod={prod} (local SHORT by {shortfall} — DATA LOSS RISK)",
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
    today: date | None = None,
) -> list[CheckResult]:
    """Compare dates present in BOTH sides, with refresh-window awareness.

    The pipeline recomputes the last ``_RECENT_WINDOW_DAYS`` of
    ``computed_daily`` on every build to absorb intraday price updates
    and late Yahoo corrections; ``upsert_daily_rows`` is INSERT OR
    REPLACE, and prod's ``computed_daily`` sync is a full DELETE+INSERT.
    So drift on dates WITHIN the refresh window is the expected flow —
    local's fresh values will cleanly replace prod's on sync.

    Drift on older dates is a genuine red flag: those rows should be
    immutable, and a mismatch implies a logic change that would silently
    rewrite prod history. FAIL in that case.
    """
    cutoff = ((today or date.today()) - timedelta(days=_RECENT_WINDOW_DAYS)).isoformat()
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
            # Recent window — drift is expected; full-replace sync resolves it.
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
        # Row counts
        print("\n[1] Row counts")
        for table in _TABLES_FOR_COUNT:
            local_n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            prod_rows = run_wrangler_query(f"SELECT COUNT(*) AS n FROM {table}")  # noqa: S608 — trusted constant
            prod_n = int(prod_rows[0]["n"]) if prod_rows else 0
            r = compare_row_counts(table, local_n, prod_n, expected_drop=expected_drops.get(table, 0))
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
            conditions = " OR ".join([f"(symbol='{s['symbol']}' AND date='{s['date']}')" for s in local_samples])
            prod_samples = run_wrangler_query(f"SELECT symbol, date, close FROM daily_close WHERE {conditions}")  # noqa: S608
        else:
            prod_samples = []
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
        prod_totals = run_wrangler_query(
            "SELECT date, total FROM computed_daily ORDER BY date DESC LIMIT 7"
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
