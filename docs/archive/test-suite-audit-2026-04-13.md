# Test Suite Audit — 2026-04-13

> **Status: executed 2026-04-13.** All six findings (T01–T06) landed in the
> `cleanup/audit-completion` PR alongside fix/A (401k warning 7-day window).
> Net impact: −144 LoC in pipeline tests, +22 Worker unit tests (was 0),
> FRED autouse fixture deletes a daily flake class, `interactive-check.spec.ts`
> no longer runs in CI.

**Scope:** All test code across `pipeline/tests/` (pytest), `src/**/*.test.{ts,tsx}` (vitest), and `e2e/*.spec.ts` (Playwright). Hunts for redundancy, dead fixtures, network leaks, and coverage gaps. Findings are **verified** via grep — each claim has file:line evidence.

**Scale reference:**
- `pipeline/tests/` — 42 files, 8,480 LoC
- `src/**/*.test.*` — 11 files, 1,412 LoC
- `e2e/*.spec.ts` — 5 files, 1,042 LoC
- **Total tests: 10,934 LoC** (vs ~11,875 LoC of production code — test:prod ≈ 0.92)

---

## Findings — ranked by ROI

### [T01] FRED tests hit live API when `FRED_API_KEY` is set — HIGH ROI ★ user-visible

**Verified.** `pipeline/etl/precompute.py:_precompute_fred` is gated only by `if not fred_key: return` (line 178). It's not mocked in tests:

- `tests/unit/test_precompute_extended.py::TestPrecomputeFred` — provides FRED data via `monkeypatch.setattr("etl.market.fred.fetch_fred_data", ...)` for 2 of 3 tests, but `TestPrecomputeMarket::test_clears_previous_data` calls `precompute_market()` end-to-end without mocking `fetch_fred_data`.
- `tests/unit/test_precompute_market.py::TestClearAndRewrite::test_rerun_does_not_duplicate` — same pattern.

**Behavior matrix:**

| Environment | `FRED_API_KEY` | Outcome |
|---|---|---|
| CI | unset | `_precompute_fred` returns early → tests pass |
| Local with `.env` (auto-loaded by `python-dotenv`) | set | live FRED call → frequent rate-limit failures |

The user's daily local pytest runs hit the FRED API and fail. Workaround so far: `--deselect` the 2 affected tests (which is how PR #133 went into review with a CI failure I would have caught locally).

- LoC impact: **+5 LoC** (one autouse fixture)
- Risk: **L** — pure deterministic-ization
- Primary use case: Y (eliminates a flake class entirely; un-blocks `pytest -q` locally)

**Action:**
```python
# tests/unit/conftest.py (or per-file)
@pytest.fixture(autouse=True)
def _no_fred_api_key(monkeypatch):
    """Force _precompute_fred to noop. Tests that want FRED data should mock
    fetch_fred_data explicitly."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)
```

---

### [T02] Four dead helpers/fixtures in conftest.py — HIGH ROI

**Verified.** Grep across `pipeline/tests/` (excluding the conftest files themselves) returns zero callers for:

| Symbol | File:line | Type |
|---|---|---|
| `positions_sample_csv` | `tests/conftest.py:20` | `@pytest.fixture` |
| `write_csv` | `tests/unit/conftest.py:42` | helper function |
| `config_file` | `tests/unit/conftest.py:68` | `@pytest.fixture` |
| `simple_csv` | `tests/unit/conftest.py:82` | `@pytest.fixture` |

Pure deletion. Half of `unit/conftest.py`'s @fixtures are unused.

- LoC impact: **−25**
- Risk: **L**
- Primary use case: Y (shrinks the auto-loaded test infrastructure)

**Action:** Delete the four definitions; no other change needed.

---

### [T03] `test_precompute_market.py` + `test_precompute_extended.py` overlap — MEDIUM ROI

**Verified.** Both files exercise `precompute_market()` and `precompute_holdings_detail()` end-to-end, with overlapping assertions:

| File | Class testing `precompute_market` | Class testing `precompute_holdings_detail` |
|---|---|---|
| `test_precompute_market.py` | `TestPrecomputeMarketRows` (4 tests), `TestSparkline` (2), `TestReturnsComputation` (4), `TestClearAndRewrite` (1), `TestSkipTickerWithTooFewRows` (3) | `TestPrecomputeHoldingsDetailRows` (~6 tests), `TestHoldingsDetailIdempotent`, `TestHoldingsDetailEmptyDB` |
| `test_precompute_extended.py` | `TestPrecomputeMarket` (4 tests — duplicates `test_precompute_market.py`'s coverage) | `TestPrecomputeHoldingsDetail` (~5 tests — overlaps) |

`test_precompute_extended.py` also has 4 unique helper-level test classes: `TestComputeIndexRow`, `TestPrecomputeIndices`, `TestPrecomputeCny`, `TestPrecomputeFred`.

The natural split:
- `test_precompute_market.py` = end-to-end (current `_market.py` + the duplicate `TestPrecomputeMarket` from `_extended.py`)
- `test_precompute_helpers.py` = individual `_precompute_*` functions (the 4 unique classes from `_extended.py`)

- LoC impact: **−200 to −300** (mostly drop the duplicate `TestPrecomputeMarket` + `TestPrecomputeHoldingsDetail` from `_extended.py`)
- Risk: **M** — mechanical move + dedup, but spans 2 files and many tests
- Primary use case: Y (clarifies test boundaries; reduces double-maintenance when `precompute_market` shape changes)

**Action:** Move helper-level tests from `test_precompute_extended.py` into a new `test_precompute_helpers.py`. Drop the duplicate end-to-end classes. Delete the old `_extended.py`.

---

### [T04] `e2e/interactive-check.spec.ts` is a manual debug tool, not a regression test — MEDIUM ROI

**Verified.** File header (line 2-4):
```
* Interactive E2E check — screenshots of every section + brush interaction.
* Run: npx playwright test e2e/interactive-check.spec.ts --headed
```

The spec writes screenshots to `test-results/screenshots/` for visual inspection. It runs in CI alongside the real regression specs (`finance.spec.ts`, `econ.spec.ts`, `fail-open.spec.ts`, `perf-brush.spec.ts`), adding ~30s to every CI run for output that no automation looks at.

- LoC impact: **−139** (delete) or **0** (exclude from CI)
- Risk: **L**
- Primary use case: depends on whether the screenshots are still consulted. If yes, exclude from CI; if not, delete.

**Action options:**
1. **Move to `e2e/manual/`** + add `testIgnore: /manual\//` to `playwright.config.ts`. Keeps the tool, removes CI cost.
2. **Delete entirely** if visual debugging via Playwright is no longer used.

---

### [T05] `test_bug_fixes.py` class names are opaque — LOW ROI

**Verified.** `tests/unit/test_bug_fixes.py:79-490` defines:

```
class TestBug1CostBasisOrder
class TestBug2HoldingPeriods
class TestBug4MissingPriceWarning   ← Bug3 missing
class TestBug5UnmappedQianjiWarning
class TestBug6TBillCusips
```

Tied to `docs/bug-report-ingestion-pipeline.md` (now at `docs/archive/ingestion-pipeline-bug-report-2026-04.md`). The numbered IDs require the reader to cross-reference that doc to understand what each suite tests. Bug3 is missing (consolidated into another fix?) — adds confusion.

- LoC impact: **0** (rename only)
- Risk: **L**
- Primary use case: N (test-name documentation only)

**Action:** Rename to behavior-descriptive names, e.g.:
- `TestBug1CostBasisOrder` → `TestCostBasisPreservedAcrossReplay`
- `TestBug4MissingPriceWarning` → `TestMissingPriceEmitsWarning`
- `TestBug5UnmappedQianjiWarning` → `TestUnmappedQianjiAccountWarns`

Drop the docstring's "TDD red phase" historical note.

---

### [T06] No unit tests for `worker/src/` or `worker-gmail/src/` — coverage gap (not redundancy)

**Verified.** Grep for `.test.ts` under `worker/` or `worker-gmail/` returns zero. All ~515 LoC of Worker code (`worker/src/index.ts:242` + `worker-gmail/src/{index,db,types}.ts:273`) is exercised only via Playwright E2E + production smoke tests.

Pure-function helpers that *could* be unit tested without a wrangler test harness:

| File | Symbol | Description |
|---|---|---|
| `worker/src/index.ts:22` | `isAllowedOrigin` | string check |
| `worker/src/index.ts:26` | `corsHeaders` | header builder |
| `worker/src/index.ts:39` | `validatedResponse<T>` | Zod validate + JSON-respond |
| `worker/src/index.ts:57` | `dbError` | error → 503 envelope |
| `worker/src/index.ts:68` | `settled` | Promise.allSettled-like wrapper |
| `worker-gmail/src/index.ts:74` | `imapOk` | response parsing |
| `worker-gmail/src/index.ts:78` | `parseSearchUid` | regex extract |

These are all pure TS, no Cloudflare-specific bindings — vitest can run them directly.

- LoC impact: **+ 100-200** (additive)
- Risk: **L** — purely additive
- Primary use case: Y (Worker is the most-deployed-untested piece)

**Action (optional):** Add `worker/src/index.test.ts` covering the 5 helpers; same for `worker-gmail/`. Don't try to test route handlers without a Worker test harness.

---

## Summary

| ID | Finding | Severity | LoC impact | Risk | Recommend |
|---|---|---|---|---|---|
| T01 | FRED hits live API in tests | **High** | +5 | L | **Execute** |
| T02 | Dead conftest fixtures | **High** | −25 | L | **Execute** |
| T03 | precompute test overlap | Medium | −200 to −300 | M | Execute (bigger refactor) |
| T04 | `interactive-check.spec.ts` in CI | Medium | −139 / 0 | L | Move to `e2e/manual/` |
| T05 | `TestBug{1,2,4,5,6}` opaque names | Low | 0 (rename) | L | Optional |
| T06 | Worker unit test coverage gap | Low | +100-200 | L | Optional (additive) |

**High-value quick wins: T01 + T02 + T04 (~10 minutes, eliminates daily FRED flake, removes 4 dead fixtures, removes 30s of CI churn).**

## What this audit ruled out

- **Frontend test suite (1,412 LoC, 11 files)** is well-organized — biggest file is `compute.test.ts` (430 LoC) testing 7+ functions, justified by surface area. No redundant assertions or dead fixtures detected.
- **No skipped tests** anywhere (`@pytest.mark.skip` / `@pytest.mark.xfail` only appears in 2 conditional skips in `tests/conftest.py:25` and `tests/unit/test_sync_diff.py:92`, both legitimate environmental gates).
- **No commented-out test code** in production paths.
- **`test_run_automation.py` (706 LoC)** and **`test_allocation.py` (702 LoC)** are large but appropriately so — both test orchestrators with high cyclomatic complexity in production code.
