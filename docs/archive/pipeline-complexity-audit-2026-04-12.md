# Pipeline Complexity Audit — 2026-04-12

**Scope:** `pipeline/etl/` + `pipeline/scripts/` only. Investigates four concerns raised during codebase walk-through: empower_401k module split, possibly-dead `portfolio.py`, three `verify_*.py` scripts' overlap, and small-module value of `incremental.py`. Findings below are **verified** via targeted grep/read — each claim has file:line evidence.

**Scale reference:**
- `pipeline/etl/` — 23 files, 4,319 LoC
- `pipeline/scripts/` — 8 files, 1,798 LoC
- `pipeline/tests/` — 42 files, 7,319 LoC

---

## Findings — ranked by ROI

### [P01] `etl/portfolio.py` is dead code in the production pipeline — HIGH ROI

**Verified.** The module's only public entry point `load_portfolio` (defined at `etl/portfolio.py:65`) has **zero production callers**. Every import is from a test file:

```
etl/portfolio.py:65           def load_portfolio(...)  # definition
tests/unit/test_portfolio.py  # 13 call sites (all test code)
```

Grep for `load_portfolio|from etl\.portfolio|from \.portfolio` across the entire `pipeline/` tree returns no matches outside `tests/unit/test_portfolio.py`. Confirmed absence in `build_timemachine_db.py`, `timemachine.py`, `allocation.py`, `reconcile.py`, `create_test_db.py`.

The work `portfolio.py` does (parse a Fidelity `Portfolio_Positions_*.csv` into per-ticker totals/counts/cost-basis) is **independently re-implemented** by `allocation._add_fidelity_positions` at `etl/allocation.py:114`, which is the live path used by the real pipeline.

The `Portfolio` TypedDict consumed by `reconcile.py` and `tests/unit/conftest.py` is defined in `etl/types.py:111`, **not** in `portfolio.py` — so removing the module does not break the type.

- LOC impact: **−89 (module) + −133 (test file) ≈ −220**
- Risk: **L** — pure deletion; no production import path; `Portfolio` TypedDict lives elsewhere
- Primary use case: Y (removes duplicate CSV-parsing logic; shrinks `etl/` surface area by ~2%)

**Action:** Delete `etl/portfolio.py` and `tests/unit/test_portfolio.py`. Leave `etl/types.py:Portfolio` TypedDict alone.

---

### [P02] `verify_qianji.py` is not a verify script — MEDIUM ROI

**Verified.** The three `scripts/verify_*.py` scripts appear parallel but `verify_qianji.py` has a fundamentally different contract:

| Script | Drift behavior | Called by automation? |
|---|---|---|
| `verify_vs_prod.py` | `sys.exit(1)` on any mismatch (`verify_vs_prod.py:266`) | Yes — `run_automation.py:235` |
| `verify_positions.py` | `return 1` on mismatch (`verify_positions.py:140-141`) | No (manual gate) |
| `verify_qianji.py` | **No exit code handling.** Only prints balances. | **No.** Zero automation callers. |

`verify_qianji.py:63-77` reads current Qianji DB balances, optionally reverse-replays to an `as_of` date, and prints a table. There is no comparison, no tolerance check, no failure path. It is a **debug/inspect tool** mislabeled as a verify gate.

This is misleading enough to warrant action: a reader (or automation PR author) could reasonably assume all three scripts are drift gates by name.

- LOC impact: **0 (rename) or +30–50 (add real gate)**
- Risk: **L**
- Primary use case: Y (prevents mis-use as a sync gate; clarifies that Qianji has no automated drift detection)

**Action options:**
1. **Rename** `verify_qianji.py` → `inspect_qianji.py` or `qianji_balances.py`. Update the one cross-reference in a comment if any exists.
2. **Add a real gate**: compare `replay_qianji()` output against DB state derived from `qianji_transactions` table; exit 1 on tolerance breach. Higher effort, adds an automation gate on par with `verify_positions.py`.

Recommend option 1 unless the user actually wants a Qianji drift gate in `run_automation.py`.

**Not redundant overall:** The three scripts' shared boilerplate (`PORTAL_DB_PATH` resolution, `sqlite3.connect`, argparse) is under 15 LoC total. Extracting a shared util would not meaningfully reduce LoC and would couple three scripts with divergent lifecycles. **Do not unify.**

---

### [P03] `etl/empower_401k.py` and `etl/ingest/empower_401k.py` — name collision, not redundancy — LOW ROI

**Verified: the split is architecturally correct.**

| File | LoC | Role | Imported by |
|---|---|---|---|
| `etl/empower_401k.py` | 259 | Compute library: QFX parse + `daily_401k_values` (proxy-ticker interpolation) | `build_timemachine_db.py:38-45` (6 symbols), `ingest/empower_401k.py:14`, tests |
| `etl/ingest/empower_401k.py` | 69 | DB adapter: `ingest_empower_qfx`, `ingest_empower_contributions` | `build_timemachine_db.py:47-50`, tests |

The split is **justified by an asymmetry with other brokers**, not arbitrary:

- **Fidelity/Robinhood** have single-file ingest because their daily values come from transaction replay in `timemachine.py` — no post-ingest compute step.
- **Empower 401k** has a compute step: quarterly QFX snapshots are externally interpolated to daily values via proxy tickers (VOO/QQQM/VXUS). `build_timemachine_db.py:336` calls `daily_401k_values(qfx_snaps, proxy_prices, ...)` — this is a live library function, not just an ingest-time parser.

Evidence the compute library has independent value: `build_timemachine_db.py:38-45` imports `PROXY_TICKERS`, `Contribution`, `QuarterSnapshot`, `daily_401k_values`, `load_all_contributions`, `load_all_qfx` — none of which belong in a DB-write adapter.

**Residual issue: name collision.** Two files named `empower_401k.py` in sibling packages make IDE navigation and imports confusing. The docstring of `ingest/empower_401k.py:2-6` literally has to explain "why there are two files with this name."

- LOC impact: **0 (rename only)**
- Risk: **L** — mechanical rename with IDE refactor
- Primary use case: N (IDE ergonomics; does not reduce LoC or improve runtime)

**Action (optional):** Rename `etl/empower_401k.py` → `etl/k401.py` or `etl/k401_compute.py`. Update imports in `build_timemachine_db.py`, `ingest/empower_401k.py`, and `tests/unit/test_empower_401k.py`. Delete the apologetic comment block from `ingest/empower_401k.py:2-6` — it exists to explain a name clash that no longer exists.

---

### [P04] `etl/incremental.py` — small single-caller module — LOW ROI

**Verified.** 56 LoC, 2 functions, 1 production caller:

```
etl/incremental.py:12   def get_last_computed_date(db_path) -> date | None   # 8 LoC body
etl/incremental.py:22   def append_daily(db_path, rows) -> int               # 35 LoC body
```

Imports:
- `scripts/build_timemachine_db.py:46` — sole production caller
- `tests/unit/test_incremental.py` — test

Both functions are query wrappers specifically scoped to `computed_daily` + `computed_daily_tickers` incremental inserts. The module exists as a pure organizational artifact; it has no reuse beyond the one caller.

**Merge options:**
1. **Into `etl/db.py` (346 LoC → ~400):** `db.py` already owns the schema for both target tables; adding incremental helpers keeps query logic next to schema. Small net reduction (one file gone). Low risk.
2. **Into `scripts/build_timemachine_db.py` (495 LoC → ~550):** inlines at the one caller, but the script is already the largest file in `scripts/` and doesn't need to grow.

- LOC impact: **−1 file, net 0 LoC** (functions relocate, not delete)
- Risk: **L**
- Primary use case: N (no LoC saved; only file-count reduction)

**Action (optional):** Merge into `etl/db.py`. Keep the test file; adjust its imports.

---

## Summary

| ID | Finding | Severity | LoC impact | Risk | Recommend |
|---|---|---|---|---|---|
| P01 | `portfolio.py` dead code | **High** | **−220** | L | **Execute** |
| P02 | `verify_qianji.py` misnamed | Medium | 0 (rename) | L | Rename (option 1) |
| P03 | Empower filename collision | Low | 0 | L | Optional rename |
| P04 | `incremental.py` single-caller | Low | 0 (file count −1) | L | Optional merge into `db.py` |

**High-value action: delete `etl/portfolio.py`** (and its test file). Everything else is stylistic/organizational.

## What this audit ruled out

- **"Three verify scripts overlap."** After reading all three in full: shared boilerplate is <15 LoC; each script has a distinct data source, comparison strategy, and dependency surface (`wrangler` subprocess vs pure SQLite vs remote Qianji DB). Extracting a shared util would couple them without meaningful savings.
- **"Pipeline is too complex overall."** 6,117 non-test LoC across 27 files supports 4 ingest sources (Fidelity/Robinhood/Empower/Qianji) + 2 market sources (Yahoo/FRED) + timemachine replay + D1 sync + verification gates. Complexity is domain-driven, not organizational bloat. The only true dead code is `portfolio.py`.
