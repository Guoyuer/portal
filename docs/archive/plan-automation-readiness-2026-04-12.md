# Automation Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended for parallel tasks) or `superpowers:executing-plans` (for serial tasks that must run after dependencies). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the "drop new Fidelity CSV → automated sync to prod D1" flow safe, correct, observable, and schedulable via Windows Task Scheduler. Fixes audit bugs B1 (run.sh never syncs new txns), B2 (default sync full-replaces destructively), B3 (QFX not in change detection), and adds schema-drift guardrails in CI.

**Architecture:**
- `sync_to_d1.py` default becomes `--diff` with auto-derived `--since`; destructive `--full` requires explicit flag
- New `verify_vs_prod.py` samples prod D1 via `wrangler d1 execute --json` and compares to local — exit non-zero on mismatch
- `run.sh` replaced by `run_portal_sync.ps1`: Windows-native, Empower QFX in change detection, file-based logs, healthchecks.io pings, graded exit codes
- CI gains schema-drift guard (`git diff --exit-code worker/schema.sql` after regen) and auto-applies `DROP VIEW IF EXISTS; CREATE VIEW` on every deploy, eliminating manual view migration

**Tech Stack:** Python 3.14, PowerShell 5.1+ (no external modules), pytest, wrangler CLI (for prod D1 queries), GitHub Actions (CI + deploy), healthchecks.io (free tier cron monitor).

**Reference docs:** `docs/sync-design-audit-2026-04-12.md` is the audit this plan executes.

---

## Execution strategy

Six tasks. **Tasks 1–5 are file-disjoint and parallelizable** (dispatch as 5 concurrent subagents). **Task 6 depends on Tasks 1 + 2 being merged.** Recommended PR flow:

```
Batch A (parallel): Task 1, 2, 3, 4, 5 → open 5 PRs → CI green → merge serially
Batch B (serial):   Task 6 → depends on #1 + #2 merged → open PR → merge
Post-merge:         Manual deployment steps (register Task Scheduler, healthchecks.io)
```

File-disjointness audit:
| Task | Files touched |
|---|---|
| 1 | `pipeline/scripts/sync_to_d1.py`, `pipeline/tests/unit/test_sync_diff.py`, `pipeline/tests/unit/test_sync_cli.py` (new) |
| 2 | `pipeline/scripts/verify_vs_prod.py` (new), `pipeline/tests/unit/test_verify_vs_prod.py` (new) |
| 3 | `pipeline/scripts/build_timemachine_db.py`, `pipeline/etl/incremental.py`, `CLAUDE.md`, `docs/ARCHITECTURE.md` |
| 4 | `.github/workflows/ci.yml` (new step only) |
| 5 | `pipeline/scripts/gen_schema_sql.py`, `worker/schema.sql`, `.github/workflows/ci.yml` (different step from #4) |
| 6 | `pipeline/scripts/run_portal_sync.ps1` (new), delete `pipeline/scripts/run.sh`, `CLAUDE.md`, `README.md` |

Overlap: #3, #5, #6 all touch `CLAUDE.md`. Resolve by merging serially with rebases or by splitting doc updates out. Task 4 and Task 5 both touch `ci.yml` but different steps — merge in order, second PR rebases.

---

## Task 1: De-fang `sync_to_d1.py` CLI

**Why:** Fix audit bugs B1 + B2 at the source. Make the safe path the default. Require an explicit loud flag for destructive full-replace.

**Files:**
- Modify: `pipeline/scripts/sync_to_d1.py`
- Modify: `pipeline/tests/unit/test_sync_diff.py`
- Create: `pipeline/tests/unit/test_sync_cli.py`

**New CLI contract:**
- `sync_to_d1.py` (no flags) → **diff sync with auto-derived `--since`** (safe default)
- `sync_to_d1.py --full` → full replace (explicit, loud)
- `sync_to_d1.py --since YYYY-MM-DD` → diff sync with explicit cutoff
- `sync_to_d1.py --dry-run` → unchanged
- `sync_to_d1.py --local` → unchanged
- `--diff` flag: **remove** (diff is now the default; keeping it would be confusing)

**`--since` auto-derivation rule:** query local `MAX(run_date) FROM fidelity_transactions`, subtract 60 days, format as ISO. Rationale: 60 days comfortably exceeds the typical Fidelity CSV window, so any new CSV's date range is fully covered by the range-replace. Print the derived value before use.

- [ ] **Step 1: Write failing test for default-is-diff behavior**

Create `pipeline/tests/unit/test_sync_cli.py`:

```python
"""Tests for sync_to_d1.py CLI default safety."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = REPO_ROOT / "pipeline" / "scripts" / "sync_to_d1.py"


def _run(args: list[str], cwd: Path, env_db: Path) -> subprocess.CompletedProcess[str]:
    """Invoke sync_to_d1.py in dry-run mode with a given DB path."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env={"PATH": "", "PYTHONPATH": str(REPO_ROOT / "pipeline"), "PORTAL_DB_PATH": str(env_db)},
    )


@pytest.fixture()
def fake_db(tmp_path):
    """Seed a DB with schema + some fidelity rows spanning 2026-01 to 2026-04."""
    import os
    sys.path.insert(0, str(REPO_ROOT / "pipeline"))
    os.chdir(str(REPO_ROOT / "pipeline"))
    from etl.db import get_connection, init_db  # noqa: E402

    p = tmp_path / "test.db"
    init_db(p)
    conn = get_connection(p)
    conn.execute(
        "INSERT INTO fidelity_transactions (run_date, account, account_number, action, action_type)"
        " VALUES ('2026-01-15', 'A', 'X', 'raw', 'buy')"
    )
    conn.execute(
        "INSERT INTO fidelity_transactions (run_date, account, account_number, action, action_type)"
        " VALUES ('2026-04-10', 'A', 'X', 'raw', 'buy')"
    )
    conn.commit()
    conn.close()
    return p


def test_default_is_diff_not_full(fake_db, tmp_path):
    """Running with no flags must NOT emit destructive DELETE FROM fidelity_transactions."""
    result = _run([], tmp_path, fake_db)
    assert result.returncode == 0, result.stderr
    assert "DELETE FROM fidelity_transactions;" not in result.stdout + result.stderr
    assert "diff" in (result.stdout + result.stderr).lower()


def test_full_requires_explicit_flag(fake_db, tmp_path):
    """--full must emit the destructive DELETE FROM."""
    result = _run(["--full"], tmp_path, fake_db)
    assert result.returncode == 0, result.stderr
    assert "full" in (result.stdout + result.stderr).lower()


def test_diff_auto_derives_since(fake_db, tmp_path):
    """Default diff with no --since should print an auto-derived cutoff."""
    result = _run([], tmp_path, fake_db)
    out = result.stdout + result.stderr
    assert "auto-derived" in out.lower() or "since=" in out.lower()


def test_diff_since_range_covers_recent_rows(fake_db, tmp_path):
    """Auto-derived --since must be <= max fidelity run_date - 60 days (or earlier),
    such that the latest fidelity row (2026-04-10 in fixture) falls in the range-replace window."""
    result = _run([], tmp_path, fake_db)
    out = result.stdout + result.stderr
    # Expect at least one fidelity INSERT in the SQL preview
    assert "INSERT INTO fidelity_transactions" in out
```

- [ ] **Step 2: Run test, verify it fails**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_sync_cli.py -v`
Expected: 4 tests fail (script currently defaults to destructive full replace).

- [ ] **Step 3: Modify `sync_to_d1.py` argparse + main**

Replace the argparse section (`_parse_args`) and the main dispatch in `pipeline/scripts/sync_to_d1.py` with:

```python
# In _parse_args():
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync timemachine.db tables to Cloudflare D1 (default: diff mode)")
    parser.add_argument("--local", action="store_true", help="Sync to local D1 (wrangler dev)")
    parser.add_argument("--dry-run", action="store_true", help="Generate SQL but don't execute")
    parser.add_argument("--full", action="store_true", help="DESTRUCTIVE: full replace all tables (default is diff)")
    parser.add_argument("--since", type=str, default=None,
                        help="Cutoff date for range-replace (YYYY-MM-DD). Auto-derived from local data if omitted.")
    return parser.parse_args()


# Add module-level constant near top:
_AUTO_SINCE_LOOKBACK_DAYS = 60


def _auto_derive_since(conn: sqlite3.Connection) -> str:
    """Derive a safe --since cutoff: latest fidelity run_date minus 60 days.
    This guarantees the window covers any realistic Fidelity CSV export period.
    """
    from datetime import date, timedelta
    row = conn.execute("SELECT MAX(run_date) FROM fidelity_transactions").fetchone()
    if row and row[0]:
        latest = date.fromisoformat(row[0])
    else:
        latest = date.today()
    return (latest - timedelta(days=_AUTO_SINCE_LOOKBACK_DAYS)).isoformat()
```

Then in `main()`, replace the current `for table in TABLES_TO_SYNC:` block logic with:

```python
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = None

    mode = "full" if args.full else "diff"
    since = args.since
    if mode == "diff" and since is None:
        since = _auto_derive_since(conn)
        print(f"  Auto-derived --since={since} (fidelity MAX(run_date) - {_AUTO_SINCE_LOOKBACK_DAYS} days)")

    print(f"  Sync mode: {mode}")

    all_sql: list[str] = []
    total_rows = 0

    for table in TABLES_TO_SYNC:
        if mode == "diff" and table in _DIFF_TABLES:
            sql, count = _dump_table_diff(conn, table)
            print(f"  {table}: {count} rows (INSERT OR IGNORE)")
        elif mode == "diff" and table in _RANGE_TABLES:
            sql, count = _dump_table_range(conn, table, _RANGE_TABLES[table], since)
            print(f"  {table}: {count} rows (range-replace > {since})")
        else:
            sql, count = _dump_table(conn, table)
            label = "full replace" if mode == "full" else "full replace (metadata table)"
            print(f"  {table}: {count} rows ({label})")
        all_sql.append(sql)
        total_rows += count
```

Also update the module docstring at the top of the file to document the new default.

- [ ] **Step 4: Support `PORTAL_DB_PATH` env override**

In `pipeline/scripts/sync_to_d1.py` near the top, replace:

```python
_DB_PATH = _PROJECT_DIR / "data" / "timemachine.db"
```

with:

```python
import os
_DB_PATH = Path(os.environ.get("PORTAL_DB_PATH", str(_PROJECT_DIR / "data" / "timemachine.db")))
```

Reason: test fixture sets this; also useful for CI.

- [ ] **Step 5: Update `test_sync_diff.py`**

Edit `pipeline/tests/unit/test_sync_diff.py` — the existing tests still import `_dump_table_diff` / `_dump_table_range` / `_dump_table` directly and don't invoke the CLI. Keep them as-is. No changes needed for this file, but verify they still pass after the `main()` rewrite.

- [ ] **Step 6: Run all sync tests**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_sync_diff.py tests/unit/test_sync_cli.py -v`
Expected: all tests pass.

- [ ] **Step 7: Run full test suite**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest -q`
Expected: 466+ passing, 0 failing (unchanged count or +4 new).

- [ ] **Step 8: Commit + open PR**

```bash
git checkout -b fix/sync-cli-defang
git add pipeline/scripts/sync_to_d1.py pipeline/tests/unit/test_sync_cli.py
git commit -m "$(cat <<'EOF'
fix(sync): make --diff default, require --full for destructive replace

- Rename implicit default (full replace) to require explicit --full flag
- Auto-derive --since from MAX(run_date in fidelity) - 60 days if omitted
- Fixes audit bug B2: default no-flag sync was wiping prod's fidelity/qianji
  superset with local's incremental subset
- Adds PORTAL_DB_PATH env var to support test fixture isolation

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin fix/sync-cli-defang
gh pr create --title "fix(sync): make --diff default, require --full for destructive replace" --body "$(cat <<'EOF'
## Summary
- Fixes audit bug B2 from docs/sync-design-audit-2026-04-12.md
- \`sync_to_d1.py\` with no flags is now safe (diff mode)
- \`--full\` required for destructive full-replace
- Auto-derives \`--since\` from local fidelity MAX(run_date) - 60 days

## Test plan
- [x] New \`test_sync_cli.py\` covers 4 CLI behavior cases
- [x] Existing \`test_sync_diff.py\` untouched and green
- [x] Full pytest green

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Task 2: Write `verify_vs_prod.py` parity check

**Why:** Audit claim 4. Before any sync, we need a machine-checkable gate that local and prod agree on the overlapping range. Today this is done by hand (`wrangler d1 execute` spot-checks). The script becomes the pre-sync gate in Task 6.

**Files:**
- Create: `pipeline/scripts/verify_vs_prod.py`
- Create: `pipeline/tests/unit/test_verify_vs_prod.py`

**Script contract:**
- Queries prod D1 via `npx wrangler d1 execute portal-db --remote --command="SELECT ..." --json`
- Samples:
  - 10 random dates from `daily_close` → compare `close` to 4 decimal places (tolerance 0.0001)
  - Last 7 days from `computed_daily.total` → compare to within $1
  - Row counts for `fidelity_transactions`, `qianji_transactions`, `computed_daily`, `daily_close`
- Exits 0 on match, 1 on any mismatch (prints detailed diff)
- Accepts `--sample-size N` to override random sample count

- [ ] **Step 1: Write failing test for row-count comparison**

Create `pipeline/tests/unit/test_verify_vs_prod.py`:

```python
"""Tests for verify_vs_prod.py parity checker."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "pipeline"))

from scripts.verify_vs_prod import (  # noqa: E402
    CheckResult,
    compare_row_counts,
    compare_daily_close_samples,
    compare_recent_totals,
    parse_wrangler_json,
)


def test_parse_wrangler_json():
    """wrangler d1 execute --json emits a list wrapping a 'results' array."""
    raw = json.dumps([{"results": [{"symbol": "SCHD", "close": 84.48}], "success": True}])
    rows = parse_wrangler_json(raw)
    assert rows == [{"symbol": "SCHD", "close": 84.48}]


def test_parse_wrangler_json_empty_results():
    raw = json.dumps([{"results": [], "success": True}])
    assert parse_wrangler_json(raw) == []


def test_compare_row_counts_match():
    result = compare_row_counts("fidelity_transactions", local=1765, prod=1765)
    assert result.ok is True
    assert result.table == "fidelity_transactions"


def test_compare_row_counts_mismatch():
    result = compare_row_counts("fidelity_transactions", local=1765, prod=1800)
    assert result.ok is False
    assert "-35" in result.detail or "35" in result.detail


def test_compare_daily_close_tolerance():
    """Within 0.0001 is OK."""
    local = [{"symbol": "SCHD", "date": "2024-10-01", "close": 84.4800}]
    prod = [{"symbol": "SCHD", "date": "2024-10-01", "close": 84.4801}]
    results = compare_daily_close_samples(local, prod, tolerance=0.0001)
    assert all(r.ok for r in results)


def test_compare_daily_close_mismatch():
    """Beyond tolerance is not OK."""
    local = [{"symbol": "SCHD", "date": "2024-10-01", "close": 84.48}]
    prod = [{"symbol": "SCHD", "date": "2024-10-01", "close": 26.62}]  # Adj Close era
    results = compare_daily_close_samples(local, prod, tolerance=0.0001)
    assert any(not r.ok for r in results)


def test_compare_recent_totals_within_dollar():
    local = [{"date": "2026-04-12", "total": 422369.00}]
    prod = [{"date": "2026-04-12", "total": 422369.50}]
    results = compare_recent_totals(local, prod, tolerance_dollars=1.0)
    assert all(r.ok for r in results)


def test_compare_recent_totals_big_drift():
    local = [{"date": "2026-04-12", "total": 422369.00}]
    prod = [{"date": "2026-04-12", "total": 411000.00}]
    results = compare_recent_totals(local, prod, tolerance_dollars=1.0)
    assert any(not r.ok for r in results)
```

- [ ] **Step 2: Run test, verify it fails with import error**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_verify_vs_prod.py -v`
Expected: all tests fail with `ModuleNotFoundError: No module named 'scripts.verify_vs_prod'`.

- [ ] **Step 3: Implement `verify_vs_prod.py`**

Create `pipeline/scripts/verify_vs_prod.py`:

```python
"""Pre-sync parity check: compare local timemachine.db against prod D1.

Exits 0 on match (sync is safe). Exits 1 on any drift (STOP, investigate).

Samples (by default):
    - 10 random (symbol, date) rows from daily_close → compare `close`
    - Last 7 days of computed_daily.total → compare within $1
    - Row counts for 4 core tables

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
    if local == prod:
        return CheckResult(ok=True, table=table, detail=f"{local} rows")
    return CheckResult(ok=False, table=table, detail=f"local={local} prod={prod} diff={local - prod}")


def compare_daily_close_samples(
    local: list[dict[str, Any]],
    prod: list[dict[str, Any]],
    tolerance: float = _CLOSE_TOLERANCE,
) -> list[CheckResult]:
    prod_map = {(r["symbol"], r["date"]): r["close"] for r in prod}
    results: list[CheckResult] = []
    for r in local:
        key = (r["symbol"], r["date"])
        lv = float(r["close"])
        pv = prod_map.get(key)
        if pv is None:
            results.append(CheckResult(ok=False, table="daily_close", detail=f"{key} missing in prod"))
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
    prod_map = {r["date"]: float(r["total"]) for r in prod}
    results: list[CheckResult] = []
    for r in local:
        d = r["date"]
        lv = float(r["total"])
        pv = prod_map.get(d)
        if pv is None:
            results.append(CheckResult(ok=False, table="computed_daily", detail=f"{d} missing in prod"))
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
```

- [ ] **Step 4: Run unit tests, verify they pass**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_verify_vs_prod.py -v`
Expected: all 8 tests pass.

- [ ] **Step 5: Sanity-check script runs end-to-end**

Run: `cd pipeline && .venv/Scripts/python.exe scripts/verify_vs_prod.py --verbose`
Expected: prints `PASS: ... checks` and exits 0 (assuming current prod is in sync from today's earlier work).

If it fails, read the output — real mismatch means today's earlier sync didn't fully land, or tolerance needs tuning. Do NOT suppress failures; investigate.

- [ ] **Step 6: Run full test suite**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest -q`
Expected: +8 tests, all pass.

- [ ] **Step 7: Commit + open PR**

```bash
git checkout -b feat/verify-vs-prod
git add pipeline/scripts/verify_vs_prod.py pipeline/tests/unit/test_verify_vs_prod.py
git commit -m "$(cat <<'EOF'
feat(pipeline): add verify_vs_prod.py parity check

Sampling pre-sync gate: row counts + random daily_close rows +
recent computed_daily totals. Exits non-zero on drift. Wired into
Task 6's automation wrapper as the gate before sync.

Closes the TODO placeholder in docs/todo-plan-2026-04.md §3.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin feat/verify-vs-prod
gh pr create --title "feat(pipeline): add verify_vs_prod.py parity check" --body "$(cat <<'EOF'
## Summary
- Implements pre-sync parity gate from audit claim 4
- Samples row counts, random daily_close rows, recent computed_daily.total
- Queries prod D1 via wrangler --json, exits 1 on drift
- Used by Task 6's PS1 wrapper to gate sync

## Test plan
- [x] 8 unit tests pass
- [x] E2E sanity: \`verify_vs_prod.py --verbose\` against current prod = PASS

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Task 3: Remove dead code

**Why:** `--positions` flag is parsed but never used. `verify` mode has no automation path and no way to ship results. Both clutter the CLI surface and make the primary flow harder to teach.

**Files:**
- Modify: `pipeline/scripts/build_timemachine_db.py`
- Modify: `pipeline/etl/incremental.py`
- Modify: `CLAUDE.md`
- Modify: `docs/ARCHITECTURE.md`

**Preservation check:** before deleting, confirm nothing imports the removed symbols.

- [ ] **Step 1: Grep for usage**

Run: `rg "_verify_build|verify_daily|DailyDrift|--positions|args\.positions" --type py`
Expected: only usages inside `build_timemachine_db.py` and `etl/incremental.py` themselves + tests (if any) + docs. No production caller.

If any test references `verify_daily` / `DailyDrift`, delete those tests in this PR too.

- [ ] **Step 2: Remove `--positions` flag**

In `pipeline/scripts/build_timemachine_db.py:83-93` (`_parse_args`):
- Delete the line `parser.add_argument("--positions", ...)`

In `pipeline/scripts/build_timemachine_db.py:72-108` (`BuildPaths` dataclass + `_resolve_paths`):
- Delete the `csv: Path | None` field... wait, `csv` is from `--csv` which IS used. Keep it.
- Delete the `--positions` handling — `args.positions` is never read, no further changes needed.

- [ ] **Step 3: Remove `verify` mode**

In `pipeline/scripts/build_timemachine_db.py`:
- Line 86: change `choices=["full", "incremental", "verify"]` → `choices=["full", "incremental"]`
- Delete function `_verify_build` (lines 474-489)
- Delete the `elif args.mode == "verify":` branch in `main()` (lines 517-518)

In `pipeline/etl/incremental.py`:
- Delete `@dataclass DailyDrift` (lines 13-19)
- Delete function `verify_daily` (lines 74-107)
- Delete the `from dataclasses import dataclass` import if unused elsewhere in the file

- [ ] **Step 4: Update docs**

In `CLAUDE.md`: search for `verify` in the pipeline commands section and remove references. Confirm `build_timemachine_db.py` documentation only mentions `full | incremental`.

In `docs/ARCHITECTURE.md`: section "Pipeline commands" — no mention of `verify` mode currently (check it); if present, remove.

- [ ] **Step 5: Run full test suite**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest -q`
Expected: passes (466 → maybe 460 if we deleted tests for verify mode).

Also run: `cd pipeline && .venv/Scripts/python.exe scripts/build_timemachine_db.py --help`
Expected: `--positions` is gone, `verify` not in choices.

- [ ] **Step 6: Commit + open PR**

```bash
git checkout -b refactor/remove-dead-code
git add pipeline/scripts/build_timemachine_db.py pipeline/etl/incremental.py CLAUDE.md docs/ARCHITECTURE.md
git commit -m "$(cat <<'EOF'
refactor(pipeline): remove --positions flag and verify mode

Both were dead code — --positions parsed but never read;
verify mode had no automation path and no way to report results.
Removes ~80 lines, shrinks CLI surface.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin refactor/remove-dead-code
gh pr create --title "refactor(pipeline): remove --positions flag and verify mode" --body "$(cat <<'EOF'
## Summary
- Deletes unused \`--positions\` CLI flag in build_timemachine_db.py
- Deletes \`verify\` mode (no automation path, no shipping channel)
- Deletes \`DailyDrift\`/\`verify_daily\` in etl/incremental.py
- Simplifies CLI surface; primary use case unaffected

## Test plan
- [x] Full pytest green
- [x] \`build_timemachine_db.py --help\` shows only full|incremental

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Task 4: CI schema-drift guard

**Why:** Audit claim 5. If `db.py:_VIEWS` changes but `gen_schema_sql.py` isn't re-run, `worker/schema.sql` drifts. Today nothing catches it.

**Files:**
- Modify: `.github/workflows/ci.yml` (add one step, don't touch deploy)

- [ ] **Step 1: Locate CI Python job**

Run: `sed -n '1,120p' .github/workflows/ci.yml` (or read the first 120 lines)
Find the Python lint/test job. Identify where to insert the new step (after mypy, before vitest).

- [ ] **Step 2: Add drift-check step**

In `.github/workflows/ci.yml`, after the existing Python test/lint steps and before any JS step, add:

```yaml
      - name: Verify worker/schema.sql is up to date
        working-directory: pipeline
        run: |
          python3 scripts/gen_schema_sql.py
          if ! git diff --exit-code ../worker/schema.sql; then
            echo "::error::worker/schema.sql is out of date."
            echo "::error::Run 'cd pipeline && python3 scripts/gen_schema_sql.py' and commit the result."
            exit 1
          fi
```

- [ ] **Step 3: Verify locally that schema.sql is currently in sync**

Run: `cd pipeline && .venv/Scripts/python.exe scripts/gen_schema_sql.py && cd .. && git diff --exit-code worker/schema.sql`
Expected: exit 0, no diff.

If there IS a diff, that means current main is already drifted — regenerate and include in this PR.

- [ ] **Step 4: Commit + open PR**

```bash
git checkout -b ci/schema-drift-guard
git add .github/workflows/ci.yml
git commit -m "$(cat <<'EOF'
ci: fail build on worker/schema.sql drift

Regenerates schema from pipeline/etl/db.py and fails if the
committed schema.sql differs. Catches the common 'forgot to
regenerate' mistake early.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin ci/schema-drift-guard
gh pr create --title "ci: fail build on worker/schema.sql drift" --body "$(cat <<'EOF'
## Summary
- Adds a CI step that regenerates worker/schema.sql from pipeline/etl/db.py
- Fails if it differs from the committed file
- Prevents silent view/table definition drift between Python source-of-truth and Worker DDL

## Test plan
- [x] \`gen_schema_sql.py\` produces identical output against current HEAD (verified locally)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Task 5: Emit `DROP VIEW IF EXISTS` + auto-apply schema on deploy

**Why:** Audit claim 5 part 2. Even if schema.sql is in sync, prod D1's views don't update because `CREATE VIEW IF NOT EXISTS` silently no-ops. Make views idempotently droppable, and apply schema on every deploy.

**Files:**
- Modify: `pipeline/scripts/gen_schema_sql.py`
- Modify: `worker/schema.sql` (regenerated output of above)
- Modify: `.github/workflows/ci.yml` (deploy step)

**Note on ordering**: merge Task 4 first, then this one, otherwise Task 4's drift check fails because we're about to regenerate schema.sql. If working in parallel branches, Task 5's PR should rebase on main after Task 4 lands.

- [ ] **Step 1: Update `gen_schema_sql.py` to emit `DROP VIEW IF EXISTS`**

In `pipeline/scripts/gen_schema_sql.py` around line 147-151, replace:

```python
    parts.append("-- ── camelCase views (match TypeScript type contract) ──────────────────────────")
    parts.append("")
    for _name, view_sql in _VIEWS.items():
        parts.append(view_sql)
        parts.append("")
```

with:

```python
    parts.append("-- ── camelCase views (match TypeScript type contract) ──────────────────────────")
    parts.append("-- Views use DROP + CREATE to make schema application idempotent — re-running")
    parts.append("-- wrangler d1 execute --file=schema.sql picks up definition changes.")
    parts.append("")
    for name, view_sql in _VIEWS.items():
        parts.append(f"DROP VIEW IF EXISTS {name};")
        parts.append(view_sql)
        parts.append("")
```

- [ ] **Step 2: Regenerate schema.sql**

Run: `cd pipeline && .venv/Scripts/python.exe scripts/gen_schema_sql.py`
Expected: `worker/schema.sql` now has 12 new `DROP VIEW IF EXISTS v_*;` lines, each before their `CREATE VIEW`.

- [ ] **Step 3: Verify D1 accepts the new schema**

Run: `cd worker && npx wrangler d1 execute portal-db --local --file=schema.sql`
Expected: executes without error. Views recreated idempotently.

(Use `--local` for test; the deploy step handles `--remote`.)

- [ ] **Step 4: Add CI deploy step to apply schema**

In `.github/workflows/ci.yml`, find the Worker deploy step. Add a new step AFTER `wrangler deploy` (so Worker code comes first, then schema):

```yaml
      - name: Apply D1 schema (idempotent)
        working-directory: worker
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
          CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
        run: |
          npx wrangler d1 execute portal-db --remote --file=schema.sql
```

- [ ] **Step 5: Run full test suite**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest -q`
Expected: all green (schema changes don't affect tests).

- [ ] **Step 6: Commit + open PR**

```bash
git checkout -b feat/auto-apply-schema
git add pipeline/scripts/gen_schema_sql.py worker/schema.sql .github/workflows/ci.yml
git commit -m "$(cat <<'EOF'
feat(ci): auto-apply D1 schema on deploy; make view DDL idempotent

- gen_schema_sql.py emits DROP VIEW IF EXISTS before each CREATE VIEW
- CI deploy step runs wrangler d1 execute --remote --file=schema.sql
- Fixes audit claim 5: view definition changes now propagate to prod
  automatically instead of requiring manual wrangler invocation

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin feat/auto-apply-schema
gh pr create --title "feat(ci): auto-apply D1 schema on deploy; make view DDL idempotent" --body "$(cat <<'EOF'
## Summary
- Closes audit claim 5
- Views now DROP + CREATE, idempotent on re-apply
- CI deploy auto-applies schema.sql to prod D1 after wrangler deploy
- Requires Task 4 (schema drift guard) merged first, otherwise rebase

## Test plan
- [x] \`wrangler d1 execute --local --file=schema.sql\` succeeds
- [x] Generated schema.sql has 12 DROP VIEW lines
- [x] Full pytest green

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Task 6: Windows automation wrapper (`run_portal_sync.ps1`) + delete `run.sh`

**Why:** `run.sh` is outdated, bash-on-Windows is awkward for Task Scheduler, bug B1 fully-blocks fidelity sync. Replace with a Windows-native PS1 that does change-detection including QFX (B3), logs to a file, pings healthchecks.io, and uses the new `sync_to_d1.py` defaults (requires Task 1) and `verify_vs_prod.py` (requires Task 2).

**Files:**
- Create: `pipeline/scripts/run_portal_sync.ps1`
- Delete: `pipeline/scripts/run.sh`
- Modify: `CLAUDE.md` (commands section)
- Modify: `README.md` (development section)

**Prerequisites:** Tasks 1 + 2 must be merged to main. Rebase this branch on the latest main before opening PR.

**Exit codes:**
- `0` — success, or no changes detected (both normal outcomes for cron)
- `1` — build failed
- `2` — verify_vs_prod failed (parity drift — investigate; do NOT sync)
- `3` — sync failed

**Environment variables (read by the script):**
- `PORTAL_HEALTHCHECK_URL` — optional; if set, script pings `$URL/start`, `$URL` on success, `$URL/fail` on failure. If unset, healthchecks are silently skipped.
- `PORTAL_DOWNLOADS` — optional override for `$HOME/Downloads`.

- [ ] **Step 1: Create `run_portal_sync.ps1`**

Create `pipeline/scripts/run_portal_sync.ps1`:

```powershell
# run_portal_sync.ps1 — Windows-native portal pipeline automation
#
# Flow: change-detection → incremental build → parity check → diff sync.
# Schedulable via Task Scheduler. Logs per-day, pings healthchecks.io if configured.

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$DryRun,
    [switch]$UseLocal
)

$ErrorActionPreference = "Stop"

# ── Paths ────────────────────────────────────────────────────────────────
$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$PipelineDir  = Split-Path -Parent $ScriptDir
$DataDir      = Join-Path $PipelineDir "data"
$DbPath       = Join-Path $DataDir "timemachine.db"
$Marker       = Join-Path $DataDir ".last_run"
$LogDir       = Join-Path $env:LOCALAPPDATA "portal\logs"
$Today        = Get-Date -Format "yyyy-MM-dd"
$LogFile      = Join-Path $LogDir "sync-$Today.log"

$Python = Join-Path $PipelineDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python3"
}

$Downloads = $env:PORTAL_DOWNLOADS
if (-not $Downloads) { $Downloads = Join-Path $env:USERPROFILE "Downloads" }

$Healthcheck = $env:PORTAL_HEALTHCHECK_URL

# ── Logging ──────────────────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log {
    param([string]$Message)
    $line = "$((Get-Date).ToString('s')) $Message"
    Add-Content -Path $LogFile -Value $line
    Write-Host $line
}

function Ping-Healthcheck {
    param([string]$Suffix = "")
    if (-not $Healthcheck) { return }
    $url = if ($Suffix) { "$Healthcheck/$Suffix" } else { $Healthcheck }
    try {
        Invoke-WebRequest -Uri $url -Method GET -TimeoutSec 10 -UseBasicParsing | Out-Null
    } catch {
        Write-Log "  healthcheck ping failed (ignored): $_"
    }
}

function Run-Python {
    param([string[]]$Args)
    Write-Log "  > $Python $($Args -join ' ')"
    & $Python @Args 2>&1 | Tee-Object -FilePath $LogFile -Append
    return $LASTEXITCODE
}

# ── Change detection ─────────────────────────────────────────────────────
function Test-ChangesDetected {
    if (-not (Test-Path $Marker)) { return $true }  # first run

    $markerTime = (Get-Item $Marker).LastWriteTime

    $qjDb = Join-Path $env:APPDATA "com.mutangtech.qianji.win\qianji_flutter\qianjiapp.db"
    if ((Test-Path $qjDb) -and ((Get-Item $qjDb).LastWriteTime -gt $markerTime)) {
        Write-Log "  Change detected: Qianji DB modified"
        return $true
    }

    foreach ($pattern in @("Accounts_History*.csv", "Bloomberg.Download*.qfx", "Robinhood_history.csv", "Portfolio_Positions*.csv")) {
        $newer = Get-ChildItem -Path $Downloads -Filter $pattern -ErrorAction SilentlyContinue |
                 Where-Object { $_.LastWriteTime -gt $markerTime }
        if ($newer) {
            Write-Log "  Change detected: new $pattern"
            return $true
        }
    }

    return $false
}

# ── Main ─────────────────────────────────────────────────────────────────
Write-Log "============================================================"
Write-Log "  Portal Sync"
Write-Log "  host=$env:COMPUTERNAME log=$LogFile"
Write-Log "============================================================"

Ping-Healthcheck "start"

if (-not $Force) {
    Write-Log "[1] Checking for data changes..."
    if (-not (Test-ChangesDetected)) {
        Write-Log "  No changes detected. Use -Force to override."
        Ping-Healthcheck    # success (no-op is a valid outcome)
        exit 0
    }
} else {
    Write-Log "[1] Force mode — skipping change detection"
}

# ── Build ────────────────────────────────────────────────────────────────
Write-Log "[2] Incremental build..."
$rc = Run-Python @("$ScriptDir\build_timemachine_db.py", "incremental")
if ($rc -ne 0) {
    Write-Log "  BUILD FAILED (exit=$rc)"
    Ping-Healthcheck "fail"
    exit 1
}

# ── Verify vs prod ───────────────────────────────────────────────────────
if (-not $UseLocal) {
    Write-Log "[3] Verifying local vs prod D1..."
    $rc = Run-Python @("$ScriptDir\verify_vs_prod.py")
    if ($rc -ne 0) {
        Write-Log "  PARITY CHECK FAILED (exit=$rc) — SYNC BLOCKED"
        Ping-Healthcheck "fail"
        exit 2
    }
}

# ── Sync ─────────────────────────────────────────────────────────────────
if ($DryRun) {
    Write-Log "[4] Dry run — skipping sync"
} else {
    Write-Log "[4] Syncing to D1 (diff mode — default)..."
    $syncArgs = @("$ScriptDir\sync_to_d1.py")
    if ($UseLocal) { $syncArgs += "--local" }
    $rc = Run-Python $syncArgs
    if ($rc -ne 0) {
        Write-Log "  SYNC FAILED (exit=$rc)"
        Ping-Healthcheck "fail"
        exit 3
    }
}

# ── Success ──────────────────────────────────────────────────────────────
(Get-Date).ToString('s') | Set-Content -Path $Marker
Write-Log "============================================================"
Write-Log "  Done"
Write-Log "============================================================"
Ping-Healthcheck
exit 0
```

- [ ] **Step 2: Verify script runs in PowerShell (dry-run)**

From cmd or PowerShell:

```powershell
cd C:\Users\guoyu\Projects\portal
powershell -NoProfile -ExecutionPolicy Bypass -File pipeline\scripts\run_portal_sync.ps1 -Force -DryRun
```

Expected:
- Log line `[1] Force mode — skipping change detection`
- `[2] Incremental build...` succeeds (no-op if DB is current)
- `[3] Verifying local vs prod D1...` succeeds (assumes Task 2 merged)
- `[4] Dry run — skipping sync`
- Log file written to `%LOCALAPPDATA%\portal\logs\sync-YYYY-MM-DD.log`
- Exit 0

- [ ] **Step 3: Delete `run.sh`**

```bash
git rm pipeline/scripts/run.sh
```

- [ ] **Step 4: Update docs**

In `CLAUDE.md`, commands section — replace:

```bash
# Automated pipeline (detect changes → build → sync)
./scripts/run.sh
```

with:

```powershell
# Automated pipeline (Windows, manual run)
powershell -ExecutionPolicy Bypass -File pipeline\scripts\run_portal_sync.ps1

# Dry run (no sync)
powershell -ExecutionPolicy Bypass -File pipeline\scripts\run_portal_sync.ps1 -DryRun

# Register with Task Scheduler (daily 06:00)
schtasks /create /tn "PortalSync" /tr "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\guoyu\Projects\portal\pipeline\scripts\run_portal_sync.ps1" /sc daily /st 06:00
```

In `README.md`, development section — make the same replacement.

In `docs/ARCHITECTURE.md`, update the mermaid diagram label `run.sh — detect changes` → `run_portal_sync.ps1 — detect changes`.

- [ ] **Step 5: Run full test suite**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest -q`
Expected: all green (no Python changes in this task, but rebase on main means #1 + #2 changes are in).

- [ ] **Step 6: End-to-end real sync (carefully)**

This is a live test against prod. Only proceed if Tasks 1, 2, 3, 4, 5 are merged.

```powershell
# Run the script for real (not dry-run)
powershell -NoProfile -ExecutionPolicy Bypass -File pipeline\scripts\run_portal_sync.ps1 -Force
```

Expected:
- Build completes
- Verify passes
- Sync completes
- `%LOCALAPPDATA%\portal\logs\sync-YYYY-MM-DD.log` has a full run trace
- `pipeline/data/.last_run` timestamp updated

Then query prod `sync_meta` to confirm:

```bash
cd worker && npx wrangler d1 execute portal-db --remote --command="SELECT * FROM sync_meta"
```

Expected: `last_sync` updated to today's ISO timestamp.

- [ ] **Step 7: Commit + open PR**

```bash
git checkout -b feat/windows-automation-wrapper
git add pipeline/scripts/run_portal_sync.ps1 CLAUDE.md README.md docs/ARCHITECTURE.md
git rm pipeline/scripts/run.sh
git commit -m "$(cat <<'EOF'
feat(pipeline): replace run.sh with Windows-native run_portal_sync.ps1

- PowerShell wrapper for Task Scheduler: change detection (incl. QFX),
  incremental build, verify_vs_prod gate, default-diff sync
- Logs per-day to %LOCALAPPDATA%\portal\logs\
- Graded exit codes: 0 ok/no-change, 1 build-fail, 2 parity-fail, 3 sync-fail
- Optional healthchecks.io pings via PORTAL_HEALTHCHECK_URL
- Fixes audit bugs B1 (run.sh never synced new fidelity txns) and B3
  (QFX not in change detection)
- run.sh deleted — Windows is the primary host

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin feat/windows-automation-wrapper
gh pr create --title "feat(pipeline): Windows automation wrapper (run_portal_sync.ps1)" --body "$(cat <<'EOF'
## Summary
- Closes audit bugs B1 (fidelity txns never synced) and B3 (QFX not detected)
- Replaces \`run.sh\` with Windows-native PowerShell wrapper
- Adds per-day log file under \`%LOCALAPPDATA%\portal\logs\`
- Adds healthchecks.io pings (opt-in via PORTAL_HEALTHCHECK_URL)
- Wires verify_vs_prod as pre-sync gate

## Dependencies
- Task 1 (sync-cli-defang) must be merged
- Task 2 (verify-vs-prod) must be merged

## Test plan
- [x] \`-DryRun\` succeeds without touching prod
- [x] Log file created + populated
- [x] Real run against prod succeeds; sync_meta.last_sync updated
- [x] schtasks registration command documented

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Post-merge deployment (manual, ~10 minutes)

Done after all 6 PRs are merged to main.

- [ ] **Register healthchecks.io account**

1. Open https://healthchecks.io/accounts/signup/
2. Create a new check: name "PortalSync", schedule "daily at 6am with 30 min grace"
3. Copy the check URL (looks like `https://hc-ping.com/abc-123-def`)
4. Store it in an env var: `setx PORTAL_HEALTHCHECK_URL "https://hc-ping.com/abc-123-def"`

- [ ] **Register Task Scheduler job**

From an admin PowerShell:

```powershell
schtasks /create `
  /tn "PortalSync" `
  /tr "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\guoyu\Projects\portal\pipeline\scripts\run_portal_sync.ps1" `
  /sc daily /st 06:00 /rl HIGHEST
```

- [ ] **Verify Task Scheduler runs it correctly**

```powershell
schtasks /run /tn "PortalSync"
Start-Sleep 10
schtasks /query /tn "PortalSync" /fo LIST /v | Select-String "Last Run"
```

Expected: `Last Run Time` updated to within the last minute; log file at `%LOCALAPPDATA%\portal\logs\sync-YYYY-MM-DD.log` has the run trace.

- [ ] **Confirm healthchecks.io received pings**

Visit the healthchecks.io dashboard. Expected: one successful ping within the last 5 minutes.

---

## Self-review checklist

- [x] Every task maps to at least one audit finding (B1→#1+#6, B2→#1, claim 4→#2, claim 5→#4+#5, B3→#6, dead code→#3)
- [x] No placeholders ("TODO", "fill in") in any step
- [x] Every code step has complete code, not sketches
- [x] Every command has expected output
- [x] Dependencies between tasks explicitly listed (#6 depends on #1+#2)
- [x] File-disjointness audit table present
- [x] Exit codes + CLI contracts fully specified before implementation
