# Data Source Abstraction Implementation Plan

> **Note (2026-04-14):** This plan is historical. Some implementation details (e.g., the `--persist-to` persist-dir mechanism described below) were dropped during execution — see commit `99ad34f` for the actual regression-script architecture. Refer to `CLAUDE.md` and the code itself for current state.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the Python pipeline's investment data sources (Fidelity, Robinhood, Empower 401k) behind a unified `InvestmentSource` Protocol with a shared transaction-replay primitive, enforced by a three-tier zero-regression harness.

**Architecture:** 8 phases. Phase 1 builds the regression safety net before any code moves (baselines, fixtures, golden). Phases 2-6 migrate one source at a time — each task ends with `bash pipeline/scripts/regression.sh` green + commit. Phase 7 is documentation. Phase 8 is conditional ABC extraction — only fires if Fidelity/Robinhood overlap ≥70%.

**Tech Stack:** Python 3.11+ (`StrEnum`, dataclasses, `ClassVar`), SQLite, pytest, mypy strict, ruff, hashlib SHA256. Cloudflare Worker via `wrangler dev --local` for L3. Spec: `docs/data-source-abstraction-design-2026-04-14.md`.

**Non-negotiable:** all three regression tiers (L1 row-level real-data hash, L2 pytest golden fixture, L3 /timeline JSON hash) must stay green after every task's commit. Any diff = regression blocker.

---

## Prerequisites

Before starting:

- Branch is `feat/data-source-abstraction-spec` (spec already committed here as `a156241`). **Do not merge the spec to main before the plan lands** — keep them as one PR.
- `cd pipeline && source .venv/bin/activate` (or `.venv/Scripts/activate` on Windows)
- Verify `npx wrangler --version` works (needed for L3)
- Verify `python3 scripts/build_timemachine_db.py --help` runs without error

---

## File Structure

**New files:**
- `pipeline/etl/sources/__init__.py` — Protocol, `SourceKind`, `ActionKind`, `PriceContext`, `PositionRow`, `_REGISTRY`, `build_investment_sources`
- `pipeline/etl/sources/fidelity.py` — `FidelitySource` + `FidelitySourceConfig`
- `pipeline/etl/sources/robinhood.py` — `RobinhoodSource` + `RobinhoodSourceConfig`
- `pipeline/etl/sources/empower.py` — `EmpowerSource` + `EmpowerSourceConfig`
- `pipeline/etl/replay.py` — `PositionState` + source-agnostic `replay_transactions`
- `pipeline/scripts/regression.sh` — L1 + L3 automated harness (run before every commit)
- `pipeline/scripts/regression_baseline.sh` — L1 + L3 baseline capture (run once on pre-refactor tree)
- `pipeline/scripts/_regression_util.py` — canonical JSON dumper + hasher used by both scripts
- `pipeline/tests/regression/__init__.py`
- `pipeline/tests/regression/baseline/.gitignore` — excludes `*.json` (committed hashes only)
- `pipeline/tests/regression/baseline/*.sha256` — committed hashes
- `pipeline/tests/regression/test_pipeline_golden.py` — L2 pytest
- `pipeline/tests/fixtures/regression/fidelity.csv`
- `pipeline/tests/fixtures/regression/robinhood.csv`
- `pipeline/tests/fixtures/regression/empower_2024-06.qfx`, `empower_2024-12.qfx`
- `pipeline/tests/fixtures/regression/qianji.sqlite` (committed binary; tiny)
- `pipeline/tests/fixtures/regression/prices.csv` (deterministic offline prices)
- `pipeline/tests/fixtures/regression/config.toml` (fixture-scoped `RawConfig`)
- `pipeline/tests/fixtures/regression/golden.json` (L2 expected output)

**Modified files:**
- `pipeline/etl/db.py` — add `robinhood_transactions` table; add `action_kind` column to `fidelity_transactions`
- `pipeline/etl/allocation.py` — replace `_add_*` helpers with registry iteration; delete helpers
- `pipeline/etl/timemachine.py` — `replay_from_db` delegates to shared primitive; Fidelity-specific classification moves to `etl/sources/fidelity.py`
- `pipeline/scripts/build_timemachine_db.py` — use `build_investment_sources`; add `--prices-from-csv` flag for offline fixture runs
- `CLAUDE.md` — update architecture and commands sections

**Deleted files:**
- `pipeline/etl/ingest/fidelity_history.py`
- `pipeline/etl/ingest/robinhood_history.py`
- `pipeline/etl/ingest/empower_401k.py`
- (`pipeline/etl/k401.py` — partially absorbed; survives if it contains reusable QFX parsing helpers, otherwise deleted)

---

## Phase 1 — Regression Safety Net

All of Phase 1 completes before any migration code runs. Once baselines are captured, the rest of the plan is driven by "regression green after every task".

### Task 1: Create regression harness scripts (no baselines yet)

**Files:**
- Create: `pipeline/scripts/_regression_util.py`
- Create: `pipeline/scripts/regression.sh`
- Create: `pipeline/scripts/regression_baseline.sh`
- Create: `pipeline/tests/regression/baseline/.gitignore`

- [ ] **Step 1: Create canonical-JSON hasher utility**

```python
# pipeline/scripts/_regression_util.py
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
```

- [ ] **Step 2: Create L1+L3 harness script**

```bash
# pipeline/scripts/regression.sh
#!/usr/bin/env bash
# Regression check: run after every task in the migration. Exits non-zero on any diff.
set -euo pipefail

cd "$(dirname "$0")/.."
BASELINE_DIR="tests/regression/baseline"
DB_PATH="${DB_PATH:-data/timemachine.db}"

# Reminder: wrangler comes from the top-level repo's worker/ package.
WRANGLER_CWD="../worker"

# ── Step 1: rebuild ─────────────────────────────────────────────────────
echo "[regression] rebuilding timemachine.db..."
.venv/Scripts/python.exe scripts/build_timemachine_db.py

# ── Step 2: L1 row-level hash compare ───────────────────────────────────
echo "[regression] L1: hashing computed_daily* ..."
.venv/Scripts/python.exe scripts/_regression_util.py compare "$DB_PATH" "$BASELINE_DIR"

# ── Step 3: L3 /timeline JSON hash compare ──────────────────────────────
echo "[regression] L3: starting wrangler --local ..."
pushd "$WRANGLER_CWD" >/dev/null
npx wrangler dev --local --persist-to=../pipeline/data/.wrangler-regression &
WRANGLER_PID=$!
popd >/dev/null
trap 'kill $WRANGLER_PID 2>/dev/null || true' EXIT

# wait for worker to become ready (max 30s)
for _ in $(seq 1 30); do
    if curl -sf http://localhost:8787/timeline >/dev/null 2>&1; then break; fi
    sleep 1
done

BODY=$(curl -sf http://localhost:8787/timeline)
GOT=$(printf '%s' "$BODY" | sha256sum | awk '{print $1}')
EXPECTED=$(cat "$BASELINE_DIR/timeline.sha256" | tr -d '[:space:]')

if [ "$GOT" != "$EXPECTED" ]; then
    echo "REGRESSION in /timeline: expected $EXPECTED, got $GOT" >&2
    exit 1
fi
echo "[regression] L3: OK"
echo "[regression] ALL TIERS GREEN ✓"
```

- [ ] **Step 3: Create baseline-capture script**

```bash
# pipeline/scripts/regression_baseline.sh
#!/usr/bin/env bash
# One-shot: capture L1 + L3 baselines from the current tree. Run on main or on the
# pre-refactor commit before starting migration. Commits the .sha256 files.
set -euo pipefail

cd "$(dirname "$0")/.."
BASELINE_DIR="tests/regression/baseline"
DB_PATH="${DB_PATH:-data/timemachine.db}"
WRANGLER_CWD="../worker"

.venv/Scripts/python.exe scripts/build_timemachine_db.py
.venv/Scripts/python.exe scripts/_regression_util.py hash "$DB_PATH" "$BASELINE_DIR"

pushd "$WRANGLER_CWD" >/dev/null
npx wrangler dev --local --persist-to=../pipeline/data/.wrangler-regression &
WRANGLER_PID=$!
popd >/dev/null
trap 'kill $WRANGLER_PID 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
    if curl -sf http://localhost:8787/timeline >/dev/null 2>&1; then break; fi
    sleep 1
done

curl -sf http://localhost:8787/timeline | sha256sum | awk '{print $1}' > "$BASELINE_DIR/timeline.sha256"
echo "baselines captured in $BASELINE_DIR"
```

- [ ] **Step 4: Create baseline gitignore**

```gitignore
# pipeline/tests/regression/baseline/.gitignore
# Personal data lives in JSON dumps — never commit. Only commit the SHA256 hashes.
*.json
```

- [ ] **Step 5: Make scripts executable and sanity-check**

```bash
chmod +x pipeline/scripts/regression.sh pipeline/scripts/regression_baseline.sh
bash -n pipeline/scripts/regression.sh  # syntax check only
bash -n pipeline/scripts/regression_baseline.sh
```

Expected: no output (both pass syntax check).

- [ ] **Step 6: Commit**

```bash
git add pipeline/scripts/_regression_util.py pipeline/scripts/regression.sh pipeline/scripts/regression_baseline.sh pipeline/tests/regression/baseline/.gitignore
git commit -m "chore(regression): add harness scripts (no baseline yet)"
```

---

### Task 2: Add `--prices-from-csv` offline seam to build script

**Files:**
- Modify: `pipeline/scripts/build_timemachine_db.py`

**Why:** L2 pytest must run without network. We need a flag that reads prices from a committed CSV fixture instead of calling Yahoo. Real builds continue to use Yahoo by default — the flag is opt-in.

- [ ] **Step 1: Write failing test**

```python
# pipeline/tests/unit/test_build_prices_seam.py
from __future__ import annotations
import subprocess
from pathlib import Path


def test_prices_from_csv_flag_bypasses_yahoo(tmp_path: Path) -> None:
    """With --prices-from-csv, build must not attempt a Yahoo network fetch."""
    prices_csv = tmp_path / "prices.csv"
    prices_csv.write_text("date,FXAIX\n2024-01-02,150.50\n", encoding="utf-8")
    result = subprocess.run(
        [
            ".venv/Scripts/python.exe",
            "scripts/build_timemachine_db.py",
            "--prices-from-csv",
            str(prices_csv),
            "--dry-run-market",
        ],
        cwd="pipeline",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "yahoo" not in result.stderr.lower() or "skipped" in result.stderr.lower()
```

- [ ] **Step 2: Run test to see it fail**

```bash
cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_build_prices_seam.py -v
```

Expected: FAIL (unknown flag `--prices-from-csv`).

- [ ] **Step 3: Add argparse flag and wire it**

In `build_timemachine_db.py`, locate the argparse block and add:

```python
parser.add_argument(
    "--prices-from-csv",
    type=Path,
    default=None,
    help="Read prices from this CSV instead of Yahoo. "
         "CSV columns: date (YYYY-MM-DD) + one column per ticker. For test fixtures only.",
)
```

In the function that currently calls `fetch_index_returns` or the market-data step, branch:

```python
if args.prices_from_csv:
    prices = _load_prices_from_csv(args.prices_from_csv)
else:
    prices = fetch_from_yahoo(...)  # existing path
```

Add the helper at the top of the file:

```python
def _load_prices_from_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.set_index("date").sort_index()
    return df
```

- [ ] **Step 4: Run test — it passes**

```bash
cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_build_prices_seam.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/scripts/build_timemachine_db.py pipeline/tests/unit/test_build_prices_seam.py
git commit -m "feat(build): add --prices-from-csv offline seam for regression fixtures"
```

---

### Task 3: Capture L1 baseline hashes

**Files:**
- Create: `pipeline/tests/regression/baseline/computed_daily.sha256` (committed)
- Create: `pipeline/tests/regression/baseline/computed_daily_tickers.sha256` (committed)

- [ ] **Step 1: Run baseline capture**

```bash
cd pipeline
bash scripts/regression_baseline.sh
```

Expected: two `.sha256` files appear in `tests/regression/baseline/`, each containing a 64-char hex string.

- [ ] **Step 2: Verify round-trip**

```bash
bash scripts/regression.sh
```

Expected: `L1: OK` + `L3: OK` + `ALL TIERS GREEN ✓`.

- [ ] **Step 3: Commit the SHA256 files (NOT the JSON dumps — those are gitignored)**

```bash
git add pipeline/tests/regression/baseline/*.sha256
git status  # verify *.json files are NOT staged
git commit -m "chore(regression): capture L1 + L3 baselines from pre-refactor tree"
```

---

### Task 4: Build L2 fixture — Fidelity CSV

**Files:**
- Create: `pipeline/tests/fixtures/regression/fidelity.csv`

**Goal:** ~50 rows covering buy / sell / dividend / reinvestment / withdrawal / T-Bill CUSIP / stock split / cross-account (at least 2 account numbers).

- [ ] **Step 1: Write fixture**

Copy 30-50 synthetic rows modeled on real Fidelity download headers:

```csv
Run Date,Account Number,Account,Action,Symbol,Description,Type,Quantity,Price,Commission,Amount,Settlement Date
01/02/2024,X12345678,Brokerage,YOU BOUGHT,FXAIX,Fidelity 500 Index Fund,Cash,10,150.00,0,-1500.00,01/02/2024
01/15/2024,X12345678,Brokerage,DIVIDEND RECEIVED,FXAIX,Fidelity 500 Index Fund,Cash,0,0,0,2.50,01/15/2024
02/01/2024,X12345678,Brokerage,REINVESTMENT,FXAIX,Fidelity 500 Index Fund,Cash,0.015,155.00,0,-2.50,02/01/2024
03/01/2024,X12345678,Brokerage,YOU SOLD,FXAIX,Fidelity 500 Index Fund,Cash,-5,160.00,0,800.00,03/01/2024
04/01/2024,Y98765432,IRA,YOU BOUGHT,91279Q123,Treasury Bill,Cash,1000,1.00,0,-1000.00,04/01/2024
...
```

Ensure coverage:
- At least one BUY, SELL, DIVIDEND, REINVESTMENT per ticker that appears in allocation
- At least one T-Bill CUSIP (8+ digit symbol starting with a digit)
- At least one ticker present in both Fidelity AND Robinhood fixtures (for cross-source aggregation check)
- Dates span 2024 so 401k snapshots (Task 6) can bracket them

- [ ] **Step 2: Sanity check parse**

```bash
cd pipeline && .venv/Scripts/python.exe -c "
import csv
from pathlib import Path
with Path('tests/fixtures/regression/fidelity.csv').open() as f:
    rows = list(csv.DictReader(f))
print(f'rows: {len(rows)}, unique symbols: {len({r[\"Symbol\"] for r in rows})}')"
```

Expected: prints row and symbol counts; doesn't raise.

- [ ] **Step 3: Commit**

```bash
git add pipeline/tests/fixtures/regression/fidelity.csv
git commit -m "test(regression): add Fidelity L2 fixture CSV"
```

---

### Task 5: Build L2 fixture — Robinhood CSV

**Files:**
- Create: `pipeline/tests/fixtures/regression/robinhood.csv`

**Goal:** ~20 rows covering buy / sell / dividend / the `($x.xx)` negative-amount format.

- [ ] **Step 1: Write fixture**

Use Robinhood's actual column headers (check `pipeline/etl/ingest/robinhood_history.py` for the exact columns):

```csv
Activity Date,Process Date,Settle Date,Instrument,Description,Trans Code,Quantity,Price,Amount
1/5/2024,1/5/2024,1/8/2024,VTI,Vanguard Total Stock Mkt ETF,Buy,5,230.00,($1150.00)
2/10/2024,2/10/2024,2/13/2024,VTI,Vanguard Total Stock Mkt ETF,CDIV,0,0,$3.25
...
```

At least one row using `($x.xx)` format to catch the negative-amount parser.

- [ ] **Step 2: Commit**

```bash
git add pipeline/tests/fixtures/regression/robinhood.csv
git commit -m "test(regression): add Robinhood L2 fixture CSV"
```

---

### Task 6: Build L2 fixture — Empower QFX snapshots (2 files)

**Files:**
- Create: `pipeline/tests/fixtures/regression/empower_2024-06.qfx`
- Create: `pipeline/tests/fixtures/regression/empower_2024-12.qfx`

**Goal:** 2 snapshots so `positions_at` can test all three date cases: before first, between, after last.

- [ ] **Step 1: Write two minimal QFX files**

Model on Empower's actual QFX structure (check `pipeline/etl/ingest/empower_401k.py` / `k401.py` for parser expectations). Minimal two-fund snapshot:

```
OFXHEADER:100
...
<INVPOSLIST>
  <POSMF>
    <INVPOS>
      <SECID><UNIQUEID>FUND001</UNIQUEID><UNIQUEIDTYPE>CUSIP</UNIQUEIDTYPE></SECID>
      <UNITS>100.0</UNITS>
      <UNITPRICE>25.00</UNITPRICE>
      <MKTVAL>2500.00</MKTVAL>
      <DTPRICEASOF>20240630</DTPRICEASOF>
    </INVPOS>
  </POSMF>
  ...
</INVPOSLIST>
```

Second file: `20241231` date, different market value to prove snapshot boundaries matter.

- [ ] **Step 2: Verify QFX parses**

```bash
cd pipeline && .venv/Scripts/python.exe -c "
from etl.ingest.empower_401k import parse_qfx
snap = parse_qfx('tests/fixtures/regression/empower_2024-06.qfx')
print(snap)
"
```

Expected: prints parsed positions; doesn't raise.

- [ ] **Step 3: Commit**

```bash
git add pipeline/tests/fixtures/regression/empower_2024-06.qfx pipeline/tests/fixtures/regression/empower_2024-12.qfx
git commit -m "test(regression): add Empower L2 QFX fixtures"
```

---

### Task 7: Build L2 fixture — Qianji SQLite

**Files:**
- Create: `pipeline/tests/fixtures/regression/qianji.sqlite` (binary)
- Create: `pipeline/tests/fixtures/regression/_build_qianji_fixture.py` (generator, committed for reproducibility)

- [ ] **Step 1: Write generator**

```python
# pipeline/tests/fixtures/regression/_build_qianji_fixture.py
"""Deterministic builder for Qianji SQLite L2 fixture.
Committed so the binary fixture can be regenerated and reviewed."""
from __future__ import annotations
import sqlite3
from pathlib import Path

OUT = Path(__file__).parent / "qianji.sqlite"


def main() -> None:
    if OUT.exists():
        OUT.unlink()
    conn = sqlite3.connect(OUT)
    conn.executescript(
        """
        CREATE TABLE user_asset (
            id INTEGER PRIMARY KEY, name TEXT, type INTEGER, money REAL, currency TEXT
        );
        CREATE TABLE user_bill (
            id INTEGER PRIMARY KEY, type INTEGER, money REAL, time INTEGER,
            assetid INTEGER, categoryid INTEGER, remark TEXT
        );
        INSERT INTO user_asset VALUES
            (1, 'US Checking', 1, 5000.0, 'USD'),
            (2, 'CNY Savings', 1, 30000.0, 'CNY'),
            (3, 'Credit Card', 2, -500.0, 'USD');
        INSERT INTO user_bill VALUES
            (1, 0, 50.0, 1704096000, 1, 100, 'Groceries'),
            (2, 0, 200.0, 1705305600, 2, 101, '餐饮'),
            (3, 1, 2000.0, 1706515200, 1, 200, 'Salary');
        """
    )
    conn.commit()
    conn.close()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

```bash
cd pipeline && .venv/Scripts/python.exe tests/fixtures/regression/_build_qianji_fixture.py
```

- [ ] **Step 3: Commit binary + generator**

```bash
git add pipeline/tests/fixtures/regression/qianji.sqlite pipeline/tests/fixtures/regression/_build_qianji_fixture.py
git commit -m "test(regression): add Qianji L2 SQLite fixture (with generator)"
```

---

### Task 8: Build L2 fixture — deterministic prices CSV

**Files:**
- Create: `pipeline/tests/fixtures/regression/prices.csv`

- [ ] **Step 1: Write prices for every ticker in fixtures + every trading day spanned**

```csv
date,FXAIX,VTI,FUND001
2024-01-02,150.00,230.00,25.00
2024-01-03,151.00,231.50,25.10
...
2024-12-31,180.00,260.00,28.50
```

Cover every ticker that appears in fidelity.csv, robinhood.csv, empower_*.qfx. One row per business day in the fixture date range.

- [ ] **Step 2: Commit**

```bash
git add pipeline/tests/fixtures/regression/prices.csv
git commit -m "test(regression): add deterministic prices L2 fixture"
```

---

### Task 9: Build L2 fixture — config.toml

**Files:**
- Create: `pipeline/tests/fixtures/regression/config.toml`

Fixture-scoped `RawConfig` that maps to the fixture files above.

- [ ] **Step 1: Write config**

```toml
# pipeline/tests/fixtures/regression/config.toml
[fidelity]
downloads_dir = "tests/fixtures/regression"
accounts = { "X12345678" = "FZFXX", "Y98765432" = "SPAXX" }
mutual_funds = ["FXAIX"]

[robinhood]
csv = "tests/fixtures/regression/robinhood.csv"

[empower]
downloads_dir = "tests/fixtures/regression"
cusip_map = { "FUND001" = "SPY" }

[qianji]
db_path = "tests/fixtures/regression/qianji.sqlite"

[ticker_map]
# USD_Checking = "VMFXX"  # optional; leave empty to exercise fallback paths
```

- [ ] **Step 2: Commit**

```bash
git add pipeline/tests/fixtures/regression/config.toml
git commit -m "test(regression): add L2 fixture config.toml"
```

---

### Task 10: Generate L2 golden and add pytest test

**Files:**
- Create: `pipeline/tests/regression/__init__.py` (empty)
- Create: `pipeline/tests/regression/test_pipeline_golden.py`
- Create: `pipeline/tests/fixtures/regression/golden.json`

- [ ] **Step 1: Write pytest test (golden not yet committed)**

```python
# pipeline/tests/regression/test_pipeline_golden.py
"""L2 regression: builds timemachine.db from committed synthetic fixtures
and asserts computed_daily + computed_daily_tickers match the committed golden.
Runs in CI (no network, no wrangler)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

FIXTURE_DIR = Path("tests/fixtures/regression")
GOLDEN = FIXTURE_DIR / "golden.json"


@pytest.fixture(scope="module")
def built_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build timemachine.db against the L2 fixture inputs."""
    out = tmp_path_factory.mktemp("regression") / "timemachine.db"
    result = subprocess.run(
        [
            ".venv/Scripts/python.exe",
            "scripts/build_timemachine_db.py",
            "--config", str(FIXTURE_DIR / "config.toml"),
            "--db", str(out),
            "--prices-from-csv", str(FIXTURE_DIR / "prices.csv"),
        ],
        cwd=".",
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"build failed: {result.stderr}"
    return out


def _load_table(db_path: Path, table: str) -> list[dict]:
    # Reuse the canonical dumper for consistency with L1
    from scripts._regression_util import dump_canonical
    return json.loads(dump_canonical(db_path, table))


def test_computed_daily_matches_golden(built_db: Path) -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    actual = {"computed_daily": _load_table(built_db, "computed_daily"),
              "computed_daily_tickers": _load_table(built_db, "computed_daily_tickers")}
    assert actual == golden, "L2 regression: computed tables diverged from golden"
```

- [ ] **Step 2: Generate the golden by running the build once**

```bash
cd pipeline
.venv/Scripts/python.exe scripts/build_timemachine_db.py \
    --config tests/fixtures/regression/config.toml \
    --db /tmp/golden_build.db \
    --prices-from-csv tests/fixtures/regression/prices.csv

.venv/Scripts/python.exe -c "
import json
from scripts._regression_util import dump_canonical
from pathlib import Path
db = Path('/tmp/golden_build.db')
golden = {
    'computed_daily': json.loads(dump_canonical(db, 'computed_daily')),
    'computed_daily_tickers': json.loads(dump_canonical(db, 'computed_daily_tickers')),
}
Path('tests/fixtures/regression/golden.json').write_text(
    json.dumps(golden, indent=2, sort_keys=True), encoding='utf-8'
)
print('golden written')
"
```

- [ ] **Step 3: Run L2 test — it passes**

```bash
cd pipeline && .venv/Scripts/python.exe -m pytest tests/regression/test_pipeline_golden.py -v
```

Expected: PASS.

- [ ] **Step 4: Run L1+L3 — still green**

```bash
cd pipeline && bash scripts/regression.sh
```

Expected: `ALL TIERS GREEN ✓`.

- [ ] **Step 5: Commit**

```bash
git add pipeline/tests/regression/__init__.py pipeline/tests/regression/test_pipeline_golden.py pipeline/tests/fixtures/regression/golden.json
git commit -m "test(regression): L2 golden test + committed golden.json"
```

---

**Phase 1 complete.** At this point:
- L1 + L3 baselines committed; `bash scripts/regression.sh` gates every future task.
- L2 fixture + golden committed; `pytest tests/regression/test_pipeline_golden.py` runs in CI.
- No production code has changed yet.

---

## Phase 2 — Foundation (Spec Migration Step 2)

### Task 11: Scaffold `etl/sources/__init__.py` — Protocol + enums + dataclasses

**Files:**
- Create: `pipeline/etl/sources/__init__.py`
- Create: `pipeline/tests/unit/test_sources_protocol.py`

- [ ] **Step 1: Write failing test for module imports + Protocol shape**

```python
# pipeline/tests/unit/test_sources_protocol.py
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etl.sources import (
    ActionKind,
    InvestmentSource,
    PositionRow,
    PriceContext,
    SourceKind,
    _REGISTRY,
    build_investment_sources,
)


def test_source_kind_is_str_enum() -> None:
    assert SourceKind.FIDELITY == "fidelity"
    assert str(SourceKind.ROBINHOOD) == "robinhood"
    assert set(SourceKind) == {SourceKind.FIDELITY, SourceKind.ROBINHOOD, SourceKind.EMPOWER}


def test_action_kind_is_str_enum() -> None:
    for k in ("BUY", "SELL", "DIVIDEND", "REINVESTMENT", "WITHDRAWAL", "DEPOSIT"):
        assert hasattr(ActionKind, k)


def test_position_row_defaults() -> None:
    row = PositionRow(ticker="FXAIX", value_usd=1500.0)
    assert row.quantity is None
    assert row.cost_basis_usd is None
    assert row.account is None


def test_price_context_required_fields() -> None:
    ctx = PriceContext(prices=pd.DataFrame(), price_date=date(2024, 1, 2), mf_price_date=date(2024, 1, 1))
    assert ctx.price_date == date(2024, 1, 2)


def test_registry_starts_empty() -> None:
    assert _REGISTRY == []


def test_build_investment_sources_returns_empty_list_for_empty_registry() -> None:
    assert build_investment_sources({}, Path("/tmp/x.db")) == []
```

- [ ] **Step 2: Run it — FAIL**

```bash
cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_sources_protocol.py -v
```

Expected: FAIL (module `etl.sources` not found).

- [ ] **Step 3: Implement the module**

```python
# pipeline/etl/sources/__init__.py
"""Investment source registry and shared protocol.

Architecture rule: all source-specific logic lives in etl/sources/<name>.py.
This module contains only the shared types and the registry list.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Protocol

import pandas as pd


class SourceKind(StrEnum):
    FIDELITY = "fidelity"
    ROBINHOOD = "robinhood"
    EMPOWER = "empower"


class ActionKind(StrEnum):
    """Normalized transaction action types. Each source translates its raw
    action strings (e.g. 'YOU BOUGHT', 'Buy') into one of these at ingest time."""
    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"
    REINVESTMENT = "reinvestment"
    WITHDRAWAL = "withdrawal"
    DEPOSIT = "deposit"
    TRANSFER = "transfer"
    OTHER = "other"


@dataclass(frozen=True)
class PriceContext:
    prices: pd.DataFrame
    price_date: date
    mf_price_date: date


@dataclass(frozen=True)
class PositionRow:
    ticker: str
    value_usd: float
    quantity: float | None = None
    cost_basis_usd: float | None = None
    account: str | None = None


class InvestmentSource(Protocol):
    kind: ClassVar[SourceKind]

    def ingest(self) -> None: ...
    def positions_at(self, as_of: date, prices: PriceContext) -> list[PositionRow]: ...


# Populated by each source module registering itself. Order matters only for
# deterministic test output — it does not affect allocation correctness.
_REGISTRY: list[type[InvestmentSource]] = []


def build_investment_sources(raw: dict, db_path: Path) -> list[InvestmentSource]:
    """Instantiate every registered source with its config slice."""
    return [cls.from_raw_config(raw, db_path) for cls in _REGISTRY]  # type: ignore[attr-defined]
```

- [ ] **Step 4: Run tests — PASS**

```bash
cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_sources_protocol.py -v
```

Expected: PASS (all 6 tests).

- [ ] **Step 5: Run full regression harness — still green (registry is empty, so no behavior change)**

```bash
cd pipeline && bash scripts/regression.sh && .venv/Scripts/python.exe -m pytest tests/regression/test_pipeline_golden.py -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add pipeline/etl/sources/__init__.py pipeline/tests/unit/test_sources_protocol.py
git commit -m "feat(sources): scaffold InvestmentSource Protocol + SourceKind/ActionKind enums"
```

---

### Task 12: Build shared replay primitive in `etl/replay.py`

**Files:**
- Create: `pipeline/etl/replay.py`
- Create: `pipeline/tests/unit/test_replay_primitive.py`

- [ ] **Step 1: Write failing test**

```python
# pipeline/tests/unit/test_replay_primitive.py
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from etl.replay import PositionState, replay_transactions
from etl.sources import ActionKind


@pytest.fixture
def mini_db(tmp_path: Path) -> Path:
    """Create a tiny SQLite DB with a normalized transactions table."""
    db = tmp_path / "mini.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE mini_transactions (
            id INTEGER PRIMARY KEY,
            txn_date TEXT NOT NULL,
            action_kind TEXT NOT NULL,
            account TEXT,
            ticker TEXT NOT NULL,
            quantity REAL NOT NULL,
            amount_usd REAL NOT NULL
        );
        """
    )
    conn.executemany(
        "INSERT INTO mini_transactions (txn_date, action_kind, account, ticker, quantity, amount_usd) VALUES (?,?,?,?,?,?)",
        [
            ("2024-01-02", ActionKind.BUY.value, "A1", "FOO", 10.0, -1000.0),
            ("2024-01-03", ActionKind.BUY.value, "A1", "FOO", 5.0, -550.0),
            ("2024-02-01", ActionKind.SELL.value, "A1", "FOO", -3.0, 330.0),
            ("2024-03-01", ActionKind.DIVIDEND.value, "A1", "FOO", 0.0, 12.0),
        ],
    )
    conn.commit()
    conn.close()
    return db


def test_replay_accumulates_position_and_cost_basis(mini_db: Path) -> None:
    states = replay_transactions(mini_db, "mini_transactions", date(2024, 2, 15))
    assert set(states.keys()) == {"FOO"}
    foo = states["FOO"]
    assert foo.quantity == pytest.approx(12.0)   # 10 + 5 - 3
    # Cost basis reduced proportionally on sell: 1550 * (1 - 3/15) = 1240
    assert foo.cost_basis_usd == pytest.approx(1240.0, rel=1e-3)


def test_replay_respects_as_of_cutoff(mini_db: Path) -> None:
    states = replay_transactions(mini_db, "mini_transactions", date(2024, 1, 2))
    foo = states["FOO"]
    assert foo.quantity == pytest.approx(10.0)
    assert foo.cost_basis_usd == pytest.approx(1000.0)


def test_replay_dropped_zero_positions(mini_db: Path) -> None:
    """Fully sold-out tickers shouldn't appear in the result."""
    # Add a fully-sold ticker
    conn = sqlite3.connect(str(mini_db))
    conn.executemany(
        "INSERT INTO mini_transactions (txn_date, action_kind, account, ticker, quantity, amount_usd) VALUES (?,?,?,?,?,?)",
        [
            ("2024-01-10", ActionKind.BUY.value, "A1", "BAR", 5.0, -200.0),
            ("2024-01-20", ActionKind.SELL.value, "A1", "BAR", -5.0, 220.0),
        ],
    )
    conn.commit()
    conn.close()
    states = replay_transactions(mini_db, "mini_transactions", date(2024, 2, 15))
    assert "BAR" not in states
```

- [ ] **Step 2: Run — FAIL**

```bash
cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_replay_primitive.py -v
```

Expected: FAIL (module `etl.replay` not found).

- [ ] **Step 3: Implement the primitive**

```python
# pipeline/etl/replay.py
"""Source-agnostic transaction replay. Takes a standardized *_transactions table
with columns (id, txn_date, action_kind, account, ticker, quantity, amount_usd)
and accumulates per-ticker quantity + cost basis as of a given date.

This module has zero knowledge of SourceKind or which sources exist."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from etl.sources import ActionKind


@dataclass(frozen=True)
class PositionState:
    quantity: float
    cost_basis_usd: float


def replay_transactions(
    db_path: Path,
    table: str,
    as_of: date,
) -> dict[str, PositionState]:
    """Return {ticker: PositionState} for all tickers with non-zero quantity as of `as_of`."""
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        f"SELECT txn_date, action_kind, ticker, quantity, amount_usd "
        f"FROM {table} WHERE txn_date <= ? ORDER BY txn_date, id",
        (as_of.isoformat(),),
    ).fetchall()
    conn.close()

    qty: dict[str, float] = defaultdict(float)
    cost: dict[str, float] = defaultdict(float)

    for txn_date_str, action, ticker, q, amt in rows:
        if not ticker:
            continue
        kind = ActionKind(action)
        if kind == ActionKind.BUY or kind == ActionKind.REINVESTMENT:
            cost[ticker] += abs(amt)
            qty[ticker] += q
        elif kind == ActionKind.SELL and qty[ticker] > 0:
            sold_fraction = min(abs(q) / qty[ticker], 1.0)
            cost[ticker] -= cost[ticker] * sold_fraction
            qty[ticker] += q  # q is negative for sells
        # DIVIDEND, WITHDRAWAL, DEPOSIT, TRANSFER, OTHER: no position/cost-basis impact
        # (cash flow is computed separately if needed)

    return {
        t: PositionState(quantity=round(qty[t], 6), cost_basis_usd=round(cost[t], 2))
        for t in qty
        if abs(qty[t]) > 1e-3
    }
```

- [ ] **Step 4: Run tests — PASS**

```bash
cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_replay_primitive.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Run full regression — still green (primitive not yet wired into production path)**

```bash
cd pipeline && bash scripts/regression.sh && .venv/Scripts/python.exe -m pytest tests/regression/test_pipeline_golden.py -v
```

- [ ] **Step 6: Commit**

```bash
git add pipeline/etl/replay.py pipeline/tests/unit/test_replay_primitive.py
git commit -m "feat(replay): source-agnostic replay_transactions primitive"
```

---

### Task 13: Add `action_kind` column to `fidelity_transactions` + backfill

**Files:**
- Modify: `pipeline/etl/db.py`
- Modify: `pipeline/etl/ingest/fidelity_history.py` (temporary — will be absorbed in Task 14)
- Create: `pipeline/etl/migrations/add_fidelity_action_kind.py`

**Why:** the shared primitive (Task 12) reads `action_kind` enum values. Existing `fidelity_transactions.action` is free-form. We add a column, backfill from the existing `action` strings using Fidelity's classification rules, and have future ingests populate it directly.

- [ ] **Step 1: Update schema in `etl/db.py`**

Locate the `fidelity_transactions` CREATE TABLE and add the column:

```python
# etl/db.py — in the fidelity_transactions DDL
# Before:  action TEXT,
# After:
action TEXT,
action_kind TEXT,   -- normalized enum: 'buy' | 'sell' | 'dividend' | ...
```

- [ ] **Step 2: Write the classification function inside fidelity_history.py (temporary home)**

```python
# pipeline/etl/ingest/fidelity_history.py (add near the top)
from etl.sources import ActionKind


def classify_fidelity_action(raw: str) -> ActionKind:
    """Map Fidelity's verbose action strings to normalized ActionKind."""
    a = (raw or "").upper()
    if a.startswith("YOU BOUGHT"): return ActionKind.BUY
    if a.startswith("YOU SOLD"):   return ActionKind.SELL
    if a.startswith("REINVESTMENT"): return ActionKind.REINVESTMENT
    if "DIVIDEND" in a:            return ActionKind.DIVIDEND
    if "WITHDRAWAL" in a or "TRANSFER" in a and "OUT" in a: return ActionKind.WITHDRAWAL
    if "DEPOSIT" in a or "TRANSFER" in a and "IN" in a:     return ActionKind.DEPOSIT
    return ActionKind.OTHER
```

Update the INSERT statement in `ingest_fidelity_csv` to write `action_kind`:

```python
# find the existing INSERT; add action_kind to columns and values
conn.execute(
    "INSERT INTO fidelity_transactions (run_date, account_number, action, action_kind, symbol, ...) "
    "VALUES (?, ?, ?, ?, ?, ...)",
    (run_date, acct, action_raw, classify_fidelity_action(action_raw).value, symbol, ...),
)
```

- [ ] **Step 3: Write backfill migration**

```python
# pipeline/etl/migrations/add_fidelity_action_kind.py
"""One-shot backfill: populate fidelity_transactions.action_kind from the action column."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from etl.ingest.fidelity_history import classify_fidelity_action


def migrate(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    # Add column if missing (idempotent)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fidelity_transactions)")}
    if "action_kind" not in cols:
        conn.execute("ALTER TABLE fidelity_transactions ADD COLUMN action_kind TEXT")
    rows = conn.execute("SELECT id, action FROM fidelity_transactions WHERE action_kind IS NULL").fetchall()
    for row_id, action in rows:
        conn.execute(
            "UPDATE fidelity_transactions SET action_kind = ? WHERE id = ?",
            (classify_fidelity_action(action).value, row_id),
        )
    conn.commit()
    conn.close()
    print(f"backfilled {len(rows)} rows")


if __name__ == "__main__":
    import sys
    migrate(Path(sys.argv[1]))
```

- [ ] **Step 4: Invoke migration during build**

In `build_timemachine_db.py`, after the Fidelity ingest step, call the migration once:

```python
from etl.migrations.add_fidelity_action_kind import migrate as _migrate_fidelity_action_kind
_migrate_fidelity_action_kind(db_path)
```

- [ ] **Step 5: Run regression — must still be green (action_kind is additive, no behavior change)**

```bash
cd pipeline && bash scripts/regression.sh && .venv/Scripts/python.exe -m pytest
```

Expected: all green. If L1 or L3 diffs, check whether the new column is accidentally being included in computed_daily* (it shouldn't be — the column is on fidelity_transactions).

- [ ] **Step 6: Commit**

```bash
git add pipeline/etl/db.py pipeline/etl/ingest/fidelity_history.py pipeline/etl/migrations/add_fidelity_action_kind.py pipeline/scripts/build_timemachine_db.py
git commit -m "feat(db): add fidelity_transactions.action_kind + backfill migration"
```

---

## Phase 3 — Migrate Fidelity (Spec Migration Step 3)

### Task 14: Create `FidelitySource` and wire it through the registry

**Files:**
- Create: `pipeline/etl/sources/fidelity.py`
- Modify: `pipeline/etl/sources/__init__.py` (register)
- Create: `pipeline/tests/unit/sources/test_fidelity.py`

- [ ] **Step 1: Write failing unit tests for FidelitySource**

```python
# pipeline/tests/unit/sources/test_fidelity.py
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etl.sources import ActionKind, PriceContext
from etl.sources.fidelity import FidelitySource, FidelitySourceConfig


@pytest.fixture
def empty_config(tmp_path: Path) -> FidelitySourceConfig:
    return FidelitySourceConfig(
        downloads_dir=tmp_path,
        fidelity_accounts={"X12345678": "FZFXX"},
        mutual_funds=frozenset({"FXAIX"}),
    )


def test_kind_is_fidelity() -> None:
    from etl.sources import SourceKind
    assert FidelitySource.kind == SourceKind.FIDELITY


def test_positions_at_surfaces_cost_basis(tmp_path: Path, empty_config: FidelitySourceConfig) -> None:
    """Fidelity MUST populate cost_basis_usd on every PositionRow."""
    # Arrange: build a tiny fidelity_transactions table manually
    import sqlite3
    db = tmp_path / "tm.db"
    from etl.db import init_schema
    init_schema(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO fidelity_transactions (run_date, account_number, action, action_kind, "
        "symbol, lot_type, quantity, amount) VALUES "
        "('2024-01-02', 'X12345678', 'YOU BOUGHT FXAIX', ?, 'FXAIX', 'Cash', 10, -1500)",
        (ActionKind.BUY.value,),
    )
    conn.commit(); conn.close()

    src = FidelitySource(empty_config, db)
    prices = pd.DataFrame({"FXAIX": [150.0]}, index=pd.to_datetime(["2024-01-02"]))
    ctx = PriceContext(prices=prices, price_date=date(2024, 1, 2), mf_price_date=date(2024, 1, 2))
    rows = src.positions_at(date(2024, 1, 2), ctx)

    fxaix = [r for r in rows if r.ticker == "FXAIX"]
    assert len(fxaix) == 1
    assert fxaix[0].cost_basis_usd == pytest.approx(1500.0)
    assert fxaix[0].quantity == pytest.approx(10.0)
    assert fxaix[0].value_usd == pytest.approx(1500.0)


def test_from_raw_config_reads_keys(tmp_path: Path) -> None:
    raw = {
        "fidelity_downloads": tmp_path,
        "fidelity_accounts": {"X12345678": "FZFXX"},
        "mutual_funds": ["FXAIX"],
    }
    src = FidelitySource.from_raw_config(raw, tmp_path / "tm.db")
    assert src._config.fidelity_accounts == {"X12345678": "FZFXX"}
    assert src._config.mutual_funds == frozenset({"FXAIX"})
```

- [ ] **Step 2: Run — FAIL**

```bash
cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/sources/test_fidelity.py -v
```

Expected: FAIL (module not found).

- [ ] **Step 3: Implement `FidelitySource`**

```python
# pipeline/etl/sources/fidelity.py
"""FidelitySource — owns:
  - CSV parsing (absorbed from etl/ingest/fidelity_history.py)
  - Action classification (classify_fidelity_action — ActionKind mapping)
  - T-Bill CUSIP aggregation
  - Mutual-fund T-1 price dating
  - Cash balance → money-market fund ticker routing (per-account)
All Fidelity knowledge lives here. No Fidelity references remain in allocation.py or timemachine.py."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar

import pandas as pd

from etl.replay import replay_transactions
from etl.sources import ActionKind, InvestmentSource, PositionRow, PriceContext, SourceKind, _REGISTRY


@dataclass(frozen=True)
class FidelitySourceConfig:
    downloads_dir: Path
    fidelity_accounts: dict[str, str]     # account number → money-market fund ticker
    mutual_funds: frozenset[str]
    table: str = "fidelity_transactions"


class FidelitySource:
    kind: ClassVar[SourceKind] = SourceKind.FIDELITY

    def __init__(self, config: FidelitySourceConfig, db_path: Path) -> None:
        self._config = config
        self._db_path = db_path

    @classmethod
    def from_raw_config(cls, raw: dict, db_path: Path) -> FidelitySource:
        return cls(
            FidelitySourceConfig(
                downloads_dir=Path(raw["fidelity_downloads"]),
                fidelity_accounts=dict(raw["fidelity_accounts"]),
                mutual_funds=frozenset(raw["mutual_funds"]),
            ),
            db_path,
        )

    def ingest(self) -> None:
        """Scan downloads_dir for Accounts_History*.csv and write normalized rows.
        Copy the existing implementation from etl/ingest/fidelity_history.py::ingest_fidelity_csv
        verbatim, then:
          1. Inline the call to classify_fidelity_action per row, writing action_kind to the INSERT.
          2. Remove the old module import from call sites in this file."""
        from etl.ingest.fidelity_history import ingest_fidelity_csv  # temporary thunk
        for csv in self._config.downloads_dir.glob("Accounts_History*.csv"):
            ingest_fidelity_csv(self._db_path, csv)

    def positions_at(self, as_of: date, prices: PriceContext) -> list[PositionRow]:
        states = replay_transactions(self._db_path, self._config.table, as_of)
        rows: list[PositionRow] = []
        for ticker, st in states.items():
            if ticker and ticker[0].isdigit() and len(ticker) >= 8:
                # T-Bill CUSIP: face value, aggregated under "T-Bills"
                rows.append(PositionRow(
                    ticker="T-Bills",
                    value_usd=st.quantity,
                    quantity=st.quantity,
                    cost_basis_usd=st.cost_basis_usd,
                ))
                continue
            p_date = prices.mf_price_date if ticker in self._config.mutual_funds else prices.price_date
            if ticker in prices.prices.columns and p_date in prices.prices.index:
                price = prices.prices.loc[p_date, ticker]
                if pd.notna(price):
                    rows.append(PositionRow(
                        ticker=ticker,
                        value_usd=round(st.quantity * float(price), 2),
                        quantity=st.quantity,
                        cost_basis_usd=st.cost_basis_usd,
                    ))
        # Cash → MM fund routing
        cash_by_account = self._cash_balances_at(as_of)
        for acct, bal in cash_by_account.items():
            mm_ticker = self._config.fidelity_accounts.get(acct, "FZFXX")
            rows.append(PositionRow(
                ticker=mm_ticker,
                value_usd=bal,
                account=acct,
            ))
        return rows

    def _cash_balances_at(self, as_of: date) -> dict[str, float]:
        """Sum per-account cash flow up to as_of. Mirrors _add_fidelity_cash behavior."""
        import sqlite3
        conn = sqlite3.connect(str(self._db_path))
        rows = conn.execute(
            f"SELECT account_number, amount, action_kind, symbol, lot_type FROM {self._config.table} "
            f"WHERE run_date <= ?",
            (as_of.isoformat(),),
        ).fetchall()
        conn.close()
        cash: dict[str, float] = {}
        for acct, amt, _kind, _sym, lot_type in rows:
            if acct and lot_type != "Shares":
                cash[acct] = cash.get(acct, 0.0) + amt
        return cash


# Register this class in the central registry
_REGISTRY.append(FidelitySource)
```

- [ ] **Step 4: Run unit tests — PASS**

```bash
cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/sources/test_fidelity.py -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Regression — still green (source not yet consumed by compute_daily_allocation)**

```bash
cd pipeline && bash scripts/regression.sh && .venv/Scripts/python.exe -m pytest
```

- [ ] **Step 6: Commit**

```bash
git add pipeline/etl/sources/fidelity.py pipeline/tests/unit/sources/test_fidelity.py pipeline/tests/unit/sources/__init__.py
git commit -m "feat(sources): FidelitySource (query interface; legacy ingest still active)"
```

---

### Task 15: Switch `compute_daily_allocation` to call Fidelity through the registry

**Files:**
- Modify: `pipeline/etl/allocation.py`
- Modify: `pipeline/scripts/build_timemachine_db.py`

- [ ] **Step 1: In `allocation.py`, replace the Fidelity-specific helper calls with registry iteration**

Find the Fidelity branch in `compute_daily_allocation`. Replace:

```python
_add_fidelity_positions(ticker_values, positions, prices, price_date, mf_price_date)
_add_fidelity_cash(ticker_values, fidelity_cash, fidelity_accounts)
```

With:

```python
from etl.sources import PriceContext
from etl.sources.fidelity import FidelitySource  # ensures registration

# ── New: registry-based Fidelity query ──
for src in investment_sources:   # passed in from caller
    if src.kind != "fidelity":
        continue
    ctx = PriceContext(prices=prices, price_date=price_date, mf_price_date=mf_price_date)
    for row in src.positions_at(current, ctx):
        ticker_values[row.ticker] = ticker_values.get(row.ticker, 0.0) + row.value_usd
```

Other sources (Robinhood/401k) still go through their old `_add_*` helpers — we migrate them in Phases 4-5.

- [ ] **Step 2: Update `build_timemachine_db.py` to build sources and pass them into `compute_daily_allocation`**

```python
from etl.sources import build_investment_sources
import etl.sources.fidelity  # ensure FidelitySource registers on import

sources = build_investment_sources(raw_config, db_path)
# ... in the daily loop:
compute_daily_allocation(..., investment_sources=sources, ...)
```

- [ ] **Step 3: Regression — MUST be green**

```bash
cd pipeline && bash scripts/regression.sh && .venv/Scripts/python.exe -m pytest
```

Expected: `ALL TIERS GREEN ✓` + L2 golden pass. If diff: Fidelity's new path produces different output than the old path — check cost basis / T-Bill / MM fund routing.

- [ ] **Step 4: Commit**

```bash
git add pipeline/etl/allocation.py pipeline/scripts/build_timemachine_db.py
git commit -m "refactor(allocation): route Fidelity through FidelitySource registry"
```

---

### Task 16: Delete absorbed Fidelity code

**Files:**
- Delete: `pipeline/etl/ingest/fidelity_history.py` (entire file)
- Modify: `pipeline/etl/allocation.py` (delete `_add_fidelity_positions` and `_add_fidelity_cash`)
- Modify: `pipeline/etl/sources/fidelity.py` (inline the `ingest_fidelity_csv` thunk)

- [ ] **Step 1: Inline the ingest logic into `FidelitySource.ingest`**

Copy the full contents of `etl/ingest/fidelity_history.py::ingest_fidelity_csv` into `FidelitySource.ingest` as a private method `_ingest_one_csv(csv_path)`, adjusting signatures to use `self._db_path`. Replace the thunk call.

- [ ] **Step 2: Delete `etl/ingest/fidelity_history.py`**

```bash
git rm pipeline/etl/ingest/fidelity_history.py
```

- [ ] **Step 3: Delete `_add_fidelity_positions` and `_add_fidelity_cash` from `allocation.py`**

Locate lines 140-173 (per `rg "_add_fidelity"`) and delete. Also delete `from etl.ingest.fidelity_history import ...` imports.

- [ ] **Step 4: Grep for any remaining Fidelity references in allocation.py / timemachine.py**

```bash
cd pipeline && grep -n "fidelity\|FIDELITY\|FXAIX\|T-Bill" etl/allocation.py etl/timemachine.py
```

Expected: zero matches in allocation.py; timemachine.py may still have some (Fidelity-specific replay helpers that become unused after Task 17).

- [ ] **Step 5: Regression + mypy + ruff**

```bash
cd pipeline
bash scripts/regression.sh
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m mypy etl/ --ignore-missing-imports
.venv/Scripts/python.exe -m ruff check .
```

All must pass.

- [ ] **Step 6: Commit**

```bash
git add -u pipeline/etl/sources/fidelity.py pipeline/etl/allocation.py
git commit -m "refactor(fidelity): inline ingest into FidelitySource; delete legacy module"
```

---

## Phase 4 — Migrate Robinhood (Spec Migration Step 4)

### Task 17: Add `robinhood_transactions` table to `etl/db.py`

**Files:**
- Modify: `pipeline/etl/db.py`
- Create: `pipeline/tests/unit/test_robinhood_schema.py`

- [ ] **Step 1: Write failing schema test**

```python
# pipeline/tests/unit/test_robinhood_schema.py
from __future__ import annotations
import sqlite3
from pathlib import Path

from etl.db import init_schema


def test_robinhood_transactions_schema(tmp_path: Path) -> None:
    db = tmp_path / "tm.db"
    init_schema(db)
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(robinhood_transactions)")}
    conn.close()
    assert {"id", "txn_date", "action", "action_kind", "ticker", "quantity", "amount_usd"}.issubset(cols)
```

- [ ] **Step 2: Run — FAIL (table doesn't exist)**

- [ ] **Step 3: Add the CREATE TABLE in `etl/db.py`**

```python
# Mirror fidelity_transactions shape — aligned columns enable the shared replay primitive.
CREATE TABLE IF NOT EXISTS robinhood_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_date TEXT NOT NULL,
    action TEXT,              -- raw action string from CSV
    action_kind TEXT NOT NULL,-- normalized ActionKind
    ticker TEXT NOT NULL,
    quantity REAL NOT NULL DEFAULT 0,
    amount_usd REAL NOT NULL DEFAULT 0,
    raw_description TEXT,
    UNIQUE (txn_date, ticker, action, quantity, amount_usd)  -- dedup on re-import
);
```

- [ ] **Step 4: Run — PASS**

- [ ] **Step 5: Regression — green**

```bash
cd pipeline && bash scripts/regression.sh && .venv/Scripts/python.exe -m pytest
```

Expected: green (new empty table has no downstream effect).

- [ ] **Step 6: Commit**

```bash
git add pipeline/etl/db.py pipeline/tests/unit/test_robinhood_schema.py
git commit -m "feat(db): add robinhood_transactions table (empty until Task 18)"
```

---

### Task 18: Create `RobinhoodSource` with persistence + positions_at

**Files:**
- Create: `pipeline/etl/sources/robinhood.py`
- Create: `pipeline/tests/unit/sources/test_robinhood.py`

- [ ] **Step 1: Write failing tests**

```python
# pipeline/tests/unit/sources/test_robinhood.py
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etl.db import init_schema
from etl.sources import PriceContext, SourceKind
from etl.sources.robinhood import RobinhoodSource, RobinhoodSourceConfig


@pytest.fixture
def fixture_csv(tmp_path: Path) -> Path:
    p = tmp_path / "rh.csv"
    p.write_text(
        "Activity Date,Process Date,Settle Date,Instrument,Description,Trans Code,Quantity,Price,Amount\n"
        "1/5/2024,1/5/2024,1/8/2024,VTI,Vanguard Total Stock Mkt ETF,Buy,5,230.00,($1150.00)\n"
        "2/10/2024,2/10/2024,2/13/2024,VTI,Vanguard Total Stock Mkt ETF,CDIV,0,0,$3.25\n",
        encoding="utf-8",
    )
    return p


def test_kind(fixture_csv: Path, tmp_path: Path) -> None:
    assert RobinhoodSource.kind == SourceKind.ROBINHOOD


def test_ingest_persists_normalized_rows(fixture_csv: Path, tmp_path: Path) -> None:
    db = tmp_path / "tm.db"
    init_schema(db)
    src = RobinhoodSource(RobinhoodSourceConfig(csv_path=fixture_csv), db)
    src.ingest()
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT txn_date, action_kind, ticker, quantity, amount_usd FROM robinhood_transactions ORDER BY id"
    ).fetchall()
    conn.close()
    assert rows[0] == ("2024-01-05", "buy", "VTI", 5.0, -1150.0)   # ($x.xx) → negative
    assert rows[1] == ("2024-02-10", "dividend", "VTI", 0.0, 3.25)


def test_positions_at_with_prices(fixture_csv: Path, tmp_path: Path) -> None:
    db = tmp_path / "tm.db"
    init_schema(db)
    src = RobinhoodSource(RobinhoodSourceConfig(csv_path=fixture_csv), db)
    src.ingest()
    prices = pd.DataFrame({"VTI": [250.0]}, index=pd.to_datetime(["2024-02-10"]))
    ctx = PriceContext(prices=prices, price_date=date(2024, 2, 10), mf_price_date=date(2024, 2, 10))
    rows = src.positions_at(date(2024, 2, 10), ctx)
    vti = [r for r in rows if r.ticker == "VTI"]
    assert len(vti) == 1
    assert vti[0].quantity == pytest.approx(5.0)
    assert vti[0].value_usd == pytest.approx(1250.0)
    assert vti[0].cost_basis_usd == pytest.approx(1150.0)
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement `RobinhoodSource`**

```python
# pipeline/etl/sources/robinhood.py
"""RobinhoodSource — persists transactions to robinhood_transactions and uses shared replay.
Replaces the on-the-fly CSV replay that previously existed in etl/ingest/robinhood_history.py."""
from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import ClassVar

import pandas as pd

from etl.replay import replay_transactions
from etl.sources import ActionKind, InvestmentSource, PositionRow, PriceContext, SourceKind, _REGISTRY


_PARENS_AMOUNT = re.compile(r"^\(([\d.,]+)\)$")


def _parse_amount(raw: str) -> float:
    """Robinhood uses ($1,234.56) for negative amounts."""
    s = (raw or "").strip().lstrip("$").replace(",", "")
    if not s:
        return 0.0
    m = _PARENS_AMOUNT.match(s)
    if m:
        return -float(m.group(1).lstrip("$"))
    return float(s)


_ACTION_MAP: dict[str, ActionKind] = {
    "Buy": ActionKind.BUY,
    "Sell": ActionKind.SELL,
    "CDIV": ActionKind.DIVIDEND,
    "DRIP": ActionKind.REINVESTMENT,
    "ACH": ActionKind.DEPOSIT,
}


def classify_robinhood_action(trans_code: str) -> ActionKind:
    return _ACTION_MAP.get(trans_code.strip(), ActionKind.OTHER)


@dataclass(frozen=True)
class RobinhoodSourceConfig:
    csv_path: Path
    table: str = "robinhood_transactions"


class RobinhoodSource:
    kind: ClassVar[SourceKind] = SourceKind.ROBINHOOD

    def __init__(self, config: RobinhoodSourceConfig, db_path: Path) -> None:
        self._config = config
        self._db_path = db_path

    @classmethod
    def from_raw_config(cls, raw: dict, db_path: Path) -> RobinhoodSource:
        return cls(RobinhoodSourceConfig(csv_path=Path(raw["robinhood_csv"])), db_path)

    def ingest(self) -> None:
        if not self._config.csv_path.exists():
            return
        conn = sqlite3.connect(str(self._db_path))
        with self._config.csv_path.open(encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                d = datetime.strptime(row["Activity Date"], "%m/%d/%Y").date()
                action_raw = row["Trans Code"]
                kind = classify_robinhood_action(action_raw)
                ticker = (row.get("Instrument") or "").strip()
                quantity = float(row.get("Quantity") or 0)
                amount = _parse_amount(row.get("Amount", ""))
                conn.execute(
                    f"INSERT OR IGNORE INTO {self._config.table} "
                    "(txn_date, action, action_kind, ticker, quantity, amount_usd, raw_description) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (d.isoformat(), action_raw, kind.value, ticker, quantity, amount, row.get("Description") or ""),
                )
        conn.commit()
        conn.close()

    def positions_at(self, as_of: date, prices: PriceContext) -> list[PositionRow]:
        states = replay_transactions(self._db_path, self._config.table, as_of)
        rows: list[PositionRow] = []
        for ticker, st in states.items():
            if ticker in prices.prices.columns and prices.price_date in prices.prices.index:
                p = prices.prices.loc[prices.price_date, ticker]
                if pd.notna(p):
                    rows.append(PositionRow(
                        ticker=ticker,
                        value_usd=round(st.quantity * float(p), 2),
                        quantity=st.quantity,
                        cost_basis_usd=st.cost_basis_usd,
                    ))
        return rows


_REGISTRY.append(RobinhoodSource)
```

- [ ] **Step 4: Run unit tests — PASS**

- [ ] **Step 5: Regression — still green (not yet consumed by compute_daily_allocation)**

- [ ] **Step 6: Commit**

```bash
git add pipeline/etl/sources/robinhood.py pipeline/tests/unit/sources/test_robinhood.py
git commit -m "feat(sources): RobinhoodSource with persistence + shared replay"
```

---

### Task 19: Switch `compute_daily_allocation` to use RobinhoodSource + delete legacy path

**Files:**
- Modify: `pipeline/etl/allocation.py`
- Delete: `pipeline/etl/ingest/robinhood_history.py`

- [ ] **Step 1: In `allocation.py`, extend the registry loop to include Robinhood**

Change the Fidelity-only branch from Task 15 to cover both transaction-level sources:

```python
for src in investment_sources:
    if src.kind in ("fidelity", "robinhood"):
        ctx = PriceContext(prices=prices, price_date=price_date, mf_price_date=mf_price_date)
        for row in src.positions_at(current, ctx):
            ticker_values[row.ticker] = ticker_values.get(row.ticker, 0.0) + row.value_usd
```

Delete `_add_robinhood` (lines 216-233) and the `AllocationSources.rh_replay_fn` field.

- [ ] **Step 2: Delete the legacy ingest module**

```bash
git rm pipeline/etl/ingest/robinhood_history.py
```

Remove any imports of `robinhood_history` from `build_timemachine_db.py` and allocation.py.

- [ ] **Step 3: Add `RobinhoodSource` registration import to `build_timemachine_db.py`**

```python
import etl.sources.robinhood  # registers RobinhoodSource in _REGISTRY
```

- [ ] **Step 4: Regression — green**

```bash
cd pipeline && bash scripts/regression.sh && .venv/Scripts/python.exe -m pytest
```

Expected: green. If not — likely the Robinhood persistence produces cost basis rounding that differs from the on-the-fly path. Check and fix.

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "refactor(robinhood): route through RobinhoodSource; delete legacy ingest and rh_replay_fn"
```

---

## Phase 5 — Migrate Empower (Spec Migration Step 5)

### Task 20: Create `EmpowerSource`

**Files:**
- Create: `pipeline/etl/sources/empower.py`
- Create: `pipeline/tests/unit/sources/test_empower.py`

- [ ] **Step 1: Write failing tests covering snapshot-lookup edge cases**

```python
# pipeline/tests/unit/sources/test_empower.py
from __future__ import annotations
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etl.db import init_schema
from etl.sources import PriceContext, SourceKind
from etl.sources.empower import EmpowerSource, EmpowerSourceConfig


def test_kind() -> None:
    assert EmpowerSource.kind == SourceKind.EMPOWER


def test_positions_at_returns_latest_snapshot_at_or_before(tmp_path: Path) -> None:
    db = tmp_path / "tm.db"
    init_schema(db)
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.executemany(
        "INSERT INTO empower_funds (snapshot_date, ticker, units, unit_price, market_value) VALUES (?,?,?,?,?)",
        [
            ("2024-06-30", "SPY", 100.0, 25.0, 2500.0),
            ("2024-12-31", "SPY", 120.0, 28.0, 3360.0),
        ],
    )
    conn.commit(); conn.close()

    src = EmpowerSource(EmpowerSourceConfig(downloads_dir=tmp_path, cusip_map={}), db)
    prices = pd.DataFrame()
    ctx = PriceContext(prices=prices, price_date=date(2024, 8, 1), mf_price_date=date(2024, 8, 1))

    # August → latest ≤ Aug is June snapshot
    rows = src.positions_at(date(2024, 8, 1), ctx)
    assert any(r.ticker == "SPY" and r.value_usd == pytest.approx(2500.0) for r in rows)

    # January → before first snapshot, should return no positions
    rows_early = src.positions_at(date(2024, 1, 1), ctx)
    assert all(r.ticker != "SPY" for r in rows_early)


def test_cost_basis_is_none_for_empower() -> None:
    """Spec: EmpowerSource MAY leave cost_basis_usd=None (QFX snapshots don't track it)."""
    # [Reuses fixture from previous test; assert cost_basis_usd is None on every row]
    ...
```

- [ ] **Step 2: Implement `EmpowerSource`**

```python
# pipeline/etl/sources/empower.py
"""EmpowerSource — snapshot-level broker. Ingests QFX into empower_snapshots + empower_funds,
positions_at looks up the latest snapshot at-or-before as_of."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar

from etl.sources import InvestmentSource, PositionRow, PriceContext, SourceKind, _REGISTRY


@dataclass(frozen=True)
class EmpowerSourceConfig:
    downloads_dir: Path
    cusip_map: dict[str, str]


class EmpowerSource:
    kind: ClassVar[SourceKind] = SourceKind.EMPOWER

    def __init__(self, config: EmpowerSourceConfig, db_path: Path) -> None:
        self._config = config
        self._db_path = db_path

    @classmethod
    def from_raw_config(cls, raw: dict, db_path: Path) -> EmpowerSource:
        return cls(
            EmpowerSourceConfig(
                downloads_dir=Path(raw["empower_downloads"]),
                cusip_map=dict(raw.get("empower_cusip_map", {})),
            ),
            db_path,
        )

    def ingest(self) -> None:
        """Copy logic from etl/ingest/empower_401k.py and from etl/k401.py's parse_qfx here."""
        # [see step 3 below]
        ...

    def positions_at(self, as_of: date, prices: PriceContext) -> list[PositionRow]:
        conn = sqlite3.connect(str(self._db_path))
        latest = conn.execute(
            "SELECT MAX(snapshot_date) FROM empower_funds WHERE snapshot_date <= ?",
            (as_of.isoformat(),),
        ).fetchone()[0]
        if latest is None:
            conn.close()
            return []
        rows_db = conn.execute(
            "SELECT ticker, units, market_value FROM empower_funds WHERE snapshot_date = ?",
            (latest,),
        ).fetchall()
        conn.close()
        out: list[PositionRow] = []
        for ticker, units, mv in rows_db:
            mapped = self._config.cusip_map.get(ticker, ticker)
            out.append(PositionRow(
                ticker=mapped,
                value_usd=float(mv),
                quantity=float(units) if units is not None else None,
                cost_basis_usd=None,   # QFX doesn't provide cost basis
            ))
        return out


_REGISTRY.append(EmpowerSource)
```

- [ ] **Step 3: Absorb QFX parsing**

Copy the full contents of `etl/ingest/empower_401k.py` (all 63 lines) and relevant QFX-parsing helpers from `etl/k401.py` into `EmpowerSource.ingest` + helper methods. Delete the originals once tests pass.

- [ ] **Step 4: Run tests — PASS**

- [ ] **Step 5: Commit**

```bash
git add pipeline/etl/sources/empower.py pipeline/tests/unit/sources/test_empower.py
git commit -m "feat(sources): EmpowerSource snapshot-lookup + ingest"
```

---

### Task 21: Switch `compute_daily_allocation` to route Empower through the registry + delete legacy

**Files:**
- Modify: `pipeline/etl/allocation.py`
- Delete: `pipeline/etl/ingest/empower_401k.py`
- Modify or delete: `pipeline/etl/k401.py` (delete if fully absorbed; keep utility functions if any remain useful)

- [ ] **Step 1: In `allocation.py`, expand registry loop to include Empower**

```python
for src in investment_sources:
    # All three investment sources now go through the same code path
    ctx = PriceContext(prices=prices, price_date=price_date, mf_price_date=mf_price_date)
    for row in src.positions_at(current, ctx):
        ticker_values[row.ticker] = ticker_values.get(row.ticker, 0.0) + row.value_usd
```

This replaces the per-`kind` if-chain — now it's kind-agnostic (reinforcing Architecture Principle #1).

Delete `_add_401k` (lines 204-213).

- [ ] **Step 2: Delete legacy modules**

```bash
git rm pipeline/etl/ingest/empower_401k.py
# k401.py: inspect what's left after absorption
cd pipeline && .venv/Scripts/python.exe -c "
from etl import k401
print([x for x in dir(k401) if not x.startswith('_')])
"
# If everything was absorbed: git rm pipeline/etl/k401.py
# Otherwise: leave surviving utility functions
```

- [ ] **Step 3: Regression — green**

```bash
cd pipeline && bash scripts/regression.sh && .venv/Scripts/python.exe -m pytest
```

- [ ] **Step 4: Commit**

```bash
git add -u
git commit -m "refactor(empower): route through EmpowerSource; delete legacy ingest + _add_401k"
```

---

## Phase 6 — Cleanup (Spec Migration Step 6)

### Task 22: Remove all remaining source-specific leakage

**Files:**
- Modify: `pipeline/etl/allocation.py`
- Modify: `pipeline/etl/timemachine.py`

- [ ] **Step 1: Grep for source names in allocation.py and compute.py**

```bash
cd pipeline && grep -nE "fidelity|robinhood|empower|401k|FXAIX" etl/allocation.py etl/timemachine.py
```

Expected in allocation.py: **zero matches**. (Spec acceptance criterion.) Any matches must be moved to the corresponding `etl/sources/<name>.py`.

- [ ] **Step 2: Clean `AllocationSources` — delete all source-specific fields**

It should now contain only genuinely shared state (prices, qianji-related, cny_rate, etc.). Any Fidelity/Robinhood/401k fields are dead code.

- [ ] **Step 3: Clean `timemachine.py`**

`replay_from_db` should be a thin wrapper over `etl.replay.replay_transactions`, or deleted entirely if no one calls it anymore. Grep for usage first.

```bash
cd pipeline && grep -rn "replay_from_db" --include="*.py"
```

If only Fidelity legacy code referenced it, delete `replay_from_db` and any supporting Fidelity-specific helpers (`_replay_core` may remain if `replay()` (CSV-based) is still useful for external tooling — otherwise delete both).

- [ ] **Step 4: Regression — green**

```bash
cd pipeline && bash scripts/regression.sh && .venv/Scripts/python.exe -m pytest && .venv/Scripts/python.exe -m mypy etl/ --ignore-missing-imports && .venv/Scripts/python.exe -m ruff check .
```

All must pass.

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "refactor(allocation): remove all source-specific leakage from allocation + timemachine"
```

---

## Phase 7 — Documentation + Final Verification (Spec Migration Step 7)

### Task 23: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the Architecture section**

Find the paragraph describing the pipeline and update to reflect:

```markdown
**Data source layer**: investment sources live under `pipeline/etl/sources/` — each (Fidelity, Robinhood, Empower 401k) owns its ingest + `positions_at` implementation. Shared transaction replay is in `pipeline/etl/replay.py`. Adding a new broker: create `etl/sources/<name>.py`, add `SourceKind.<NAME>` variant, add entry to `_REGISTRY`. `compute_daily_allocation` is source-agnostic — no broker-specific code lives there. Qianji (cash + spending) stays outside the `InvestmentSource` protocol because its semantics differ.
```

- [ ] **Step 2: Update the Commands section**

Add:
```
bash pipeline/scripts/regression.sh                 # L1 + L3 regression gate (run before every commit during refactors)
bash pipeline/scripts/regression_baseline.sh        # capture new baseline (only after an approved behavior change)
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): update architecture + commands for source abstraction"
```

---

### Task 24: Full verification sweep

- [ ] **Step 1: All gates green**

```bash
cd pipeline
bash scripts/regression.sh
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m mypy etl/ --ignore-missing-imports
.venv/Scripts/python.exe -m ruff check .
```

- [ ] **Step 2: Confirm spec acceptance criteria**

Verify every criterion from the spec's "Acceptance criteria" section:

| Criterion | Check |
|---|---|
| All 3 regression tiers green | `bash scripts/regression.sh` + `pytest tests/regression/` |
| Adding a 5th source ≤4 files | grep plan: `SourceKind` (1 line), new source file (1), new table in db.py (1), `_REGISTRY` line (1) |
| `_add_fidelity_positions` / `_add_robinhood` / `_add_401k` deleted | `grep -rn "_add_fidelity\|_add_robinhood\|_add_401k" pipeline/` → empty |
| `rh_replay_fn` deleted | `grep -rn "rh_replay_fn" pipeline/` → empty |
| `CLAUDE.md` reflects new structure | visual review |
| `compute_daily_allocation` has zero source-name references | `grep -nE "fidelity\|robinhood\|empower\|401k" pipeline/etl/allocation.py` → empty |

- [ ] **Step 3: If all pass, the core migration is done.**

---

## Phase 8 — Conditional: Extract `CsvTransactionBroker` ABC (Spec Migration Step 8)

### Task 25: Measure overlap between `FidelitySource` and `RobinhoodSource`

- [ ] **Step 1: Compare `ingest` and `positions_at` method bodies**

```bash
cd pipeline && diff -u etl/sources/fidelity.py etl/sources/robinhood.py
```

Manually identify shared structural lines: CSV iteration, column mapping, action classification, INSERT into transactions table, replay invocation, row projection.

Compute overlap = `shared_lines / max(fidelity_lines, robinhood_lines)`.

- [ ] **Step 2: Decision**

- If overlap **≥ 70%**: proceed to Tasks 26 + 27.
- If overlap **< 70%**: STOP. Record the finding in a new commit (`docs(sources): decision log — ABC extraction skipped, overlap was N%`). The refactor is complete.

---

### Task 26 (conditional): Extract `CsvTransactionBroker` ABC

**Files:**
- Create: `pipeline/etl/sources/_csv_broker.py`

- [ ] **Step 1: Define the ABC with `ClassVar` declarations for what varies**

```python
# pipeline/etl/sources/_csv_broker.py
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import ClassVar

import pandas as pd

from etl.replay import replay_transactions
from etl.sources import ActionKind, PositionRow, PriceContext, SourceKind


@dataclass(frozen=True)
class CsvColumnMap:
    date: str
    action: str
    ticker: str
    quantity: str
    amount: str


class CsvTransactionBroker(ABC):
    kind: ClassVar[SourceKind]
    COLUMN_MAP: ClassVar[CsvColumnMap]
    ACTION_RULES: ClassVar[dict[str, ActionKind]]
    DATE_FORMAT: ClassVar[str] = "%m/%d/%Y"
    AMOUNT_NEGATIVE_FORMAT: ClassVar[str] = "minus"   # "minus" | "parens"

    def __init__(self, config, db_path: Path) -> None:
        self._config = config
        self._db_path = db_path

    def ingest(self) -> None:
        # generic CSV glob → parse per COLUMN_MAP → classify via ACTION_RULES → insert
        ...

    def positions_at(self, as_of: date, prices: PriceContext) -> list[PositionRow]:
        states = replay_transactions(self._db_path, self._config.table, as_of)
        return [self._project(t, s, prices) for t, s in states.items()]

    @abstractmethod
    def _project(self, ticker: str, state, prices: PriceContext) -> PositionRow: ...
    # Optional hooks for quirks (Fidelity overrides these):
    def _post_parse_row(self, row: dict) -> dict: return row
    def _post_replay_states(self, states: dict) -> dict: return states
```

- [ ] **Step 2: Migrate `RobinhoodSource` and `FidelitySource` onto the ABC**

`RobinhoodSource` becomes ~15 lines (class-level declarations + `from_raw_config` + `_project`).
`FidelitySource` retains overrides for T-Bill CUSIP / MM fund routing / T-1 pricing via hooks.

- [ ] **Step 3: Regression — green**

```bash
cd pipeline && bash scripts/regression.sh && .venv/Scripts/python.exe -m pytest
```

- [ ] **Step 4: Commit**

```bash
git add -u
git commit -m "refactor(sources): extract CsvTransactionBroker ABC (Step 8)"
```

---

### Task 27 (conditional): Validate that adding a hypothetical Schwab source is ~20 lines

- [ ] **Step 1: Mock-add a `SchwabSource` in a throwaway branch**

```python
# pipeline/etl/sources/schwab.py   (NOT committed)
from etl.sources._csv_broker import CsvTransactionBroker, CsvColumnMap
from etl.sources import ActionKind, SourceKind

class SchwabSource(CsvTransactionBroker):
    kind = SourceKind.SCHWAB
    COLUMN_MAP = CsvColumnMap(date="Date", action="Action", ticker="Symbol", quantity="Quantity", amount="Amount")
    ACTION_RULES = {"Buy": ActionKind.BUY, "Sell": ActionKind.SELL, "Reinvest Dividend": ActionKind.REINVESTMENT}
    ...
```

- [ ] **Step 2: Count lines + confirm**

If `SchwabSource` is ≤ 30 lines including config and `from_raw_config`, the ABC has paid for itself.

- [ ] **Step 3: Discard the branch** (Schwab isn't being added now).

---

## Self-Review Notes (fix inline before publishing)

**Spec coverage sweep:**
- Protocol definition → Task 11
- PriceContext + PositionRow + cost_basis_usd contract → Tasks 11, 14, 18, 20
- SourceKind StrEnum → Task 11
- `from_raw_config` classmethod pattern → Tasks 14, 18, 20
- `_REGISTRY` list → Task 11
- Shared replay primitive → Task 12
- Robinhood persistence → Tasks 17-19
- Empower snapshot-lookup → Tasks 20-21
- Qianji stays outside → no task needed (nothing moves)
- Market data stays outside → no task needed
- Architecture Principle #1 (no source logic outside `etl/sources/`) → Tasks 14-22 enforce; Task 22 greps
- L1 row-level hash → Tasks 1, 3
- L2 pytest golden → Tasks 4-10
- L3 /timeline hash automated → Task 1
- 8-step migration → Phases 1-7 = spec steps 1-7; Phase 8 = spec step 8
- Acceptance criteria → Task 24 verifies

**Type consistency:**
- `positions_at` signature `(as_of: date, prices: PriceContext) -> list[PositionRow]` consistent across tasks 11, 14, 18, 20.
- `ingest() -> None` consistent.
- `replay_transactions(db_path, table, as_of)` consistent in Tasks 12, 14, 18, 26.

**Placeholder scan:**
- Task 20 Step 3 says "Copy logic from etl/ingest/empower_401k.py and from etl/k401.py's parse_qfx here" — acceptable as a code-move instruction (engineer reads source, copies). Not a placeholder.
- Task 25 uses "overlap" subjectively — fixed by concrete formula `shared_lines / max(fidelity_lines, robinhood_lines)`.

---

## Execution Handoff

**Plan complete and saved to `docs/data-source-abstraction-plan-2026-04-14.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. Especially good here because each task is self-contained and the regression harness gives an objective green/red gate per commit.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
