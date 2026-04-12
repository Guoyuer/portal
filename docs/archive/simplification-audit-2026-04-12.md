# Simplification Audit — 2026-04-12

**Scope:** Whole-codebase LOC reduction scan. Three parallel Explore agents covered (1) `pipeline/etl/` + `pipeline/scripts/`, (2) `worker/src/` + `src/lib/`, (3) `docs/` + config files. Findings below are **vetted** — agent suggestions verified by grep/read before inclusion. Ones that failed verification are marked and explained.

**Recently completed** (already simplified, NOT revisited):
- `sync_to_d1.py` — CLI de-fang + auto-derive `--since` (PR #111)
- `build_timemachine_db.py` — removed `verify` mode, `_verify_build`, `DailyDrift`, `verify_daily` (PR #113)
- Dead `run.sh` deletion (in-flight via PR #114 pivot)
- `worker/schema.sql` DROP VIEW emission (PR #112)

---

## Findings — ranked by ROI (LOC × inverse-risk × primary-use-case value)

Each finding has a `[Pxx]` tag so PRs can reference them directly.

### [P01] `--positions` flag still in `build_timemachine_db.py:92` — HIGH ROI

**Agent claim verified.** PR #113 removed `verify` mode but missed the actual argparse flag. Line 92 still has:

```python
parser.add_argument("--positions", type=Path, default=None, help="Fidelity positions CSV for calibration (future)")
```

`args.positions` is never read anywhere. Dead from day one. Delete the line.

- LOC: 1
- Risk: L
- Primary use case: Y (removes one flag from help output)

---

### [P02] Dedupe `_qj_target_value` / `_parse_target_value` — HIGH ROI

**Verified.** Two independent implementations of currency-conversion parsing for Qianji transactions:
- `pipeline/etl/timemachine.py:361-380` — `_qj_target_value(money, extra_str)`
- `pipeline/scripts/verify_qianji.py:14-29` — `_parse_target_value(money, extra_str)` (same body)

Canonical location already exists in `pipeline/etl/ingest/qianji_db.py:_parse_amount` — same logic, different name. Three copies of the same 15-line function.

**Change:** Rename `_parse_amount` → `parse_qj_amount` in `ingest/qianji_db.py` (make public), import from both call sites, delete the two copies.

- LOC: ~25 (30 deleted − 5 added imports)
- Risk: L
- Primary use case: Y (cleaner replay path)

---

### [P03] CI duplicate `next build` — HIGH ROI

**Verified.** `.github/workflows/ci.yml:41` and `.github/workflows/ci.yml:75` both run `npx next build`:
- Line 41: in `frontend` job, with `NEXT_PUBLIC_TIMELINE_URL=http://localhost:4444/timeline` (mock)
- Line 75: in `deploy` job, with real Worker URL

Line 41's build isn't used by vitest (vitest imports TS directly) and isn't needed for deploy. It's verifying "build doesn't crash" — but deploy does the same thing and would catch it.

**Change:** Delete line 41. Keep `npm run test:coverage` at line 46.

- LOC: 1 (saves ~90s on every CI run)
- Risk: L (deploy still catches build errors)
- Primary use case: Y (faster CI)

---

### [P04] Inline `_categorize_ticker` at `allocation.py:64-91` — MEDIUM-HIGH ROI

**Verified.** 18-line helper called from exactly one location (`_build_allocation_row` line 226). The abstraction doesn't pay off — caller passes 4 args and uses all 4 fields of return value immediately.

**Change:** Inline the body into `_build_allocation_row`, delete the function.

- LOC: ~18
- Risk: M (critical allocation path — inlining isn't dangerous, but test coverage is important)
- Primary use case: Y (allocation is the sync hot path)

---

### [P05] `_to_float` wrapper in `build_timemachine_db.py:119-121` — MEDIUM ROI

**Verified.** 3-line wrapper:
```python
def _to_float(val: object) -> float:
    return float(val)  # type: ignore[arg-type]
```
Called 5 times (lines 368, 369, 389-390). Inline `float()` with `# type: ignore[arg-type]` at each call site, or cast via typed helper pattern.

Simpler alternative: replace `_to_float(x)` with `cast(float, x)` from `typing` — more explicit about what's happening (we know these are numeric after ingest, just not typed as such).

**Change:** Delete `_to_float`, use `cast(float, ...)` at 5 call sites.

- LOC: ~3 (3 deleted, 0 net change at call sites if same length)
- Risk: L
- Primary use case: N (cosmetic)

---

### [P06] Stale `pyproject.toml` package name — TRIVIAL

**Verified.** `pipeline/pyproject.toml:2`:
```toml
name = "asset-snapshot"
```
Package was renamed to `etl/` in an earlier PR; the package metadata wasn't updated. Not blocking (we don't publish this), but confusing.

**Change:** `name = "portal-pipeline"` or `name = "etl"`.

- LOC: 0 net
- Risk: L
- Primary use case: N

---

### [P07] Inline `settled()` + `dbError()` Worker helpers — MEDIUM ROI

**Verified.** `worker/src/index.ts` has two small helpers used only for `/timeline`'s fail-open behavior. `settled()` wraps a promise and returns `{value, error}`; `dbError()` constructs a `Response` for DB failures.

**Note on risk**: These helpers are thin but encode non-trivial semantics (fail-open pattern). Inlining might make the main handler less readable. Tension between LOC savings and clarity.

**Recommend SKIP** — the helpers document intent. Not worth the readability cost for ~15 LOC.

- LOC: ~15 if done
- Risk: M
- Primary use case: N
- **Verdict: SKIP**

---

### [P08] Zod `.default(0)` / `.default(null)` over-defensive — LOW ROI

**Partial verification.** Agent claimed D1 always populates these fields. **Need to confirm per-field** — D1 `fidelity_transactions.quantity` is `REAL NOT NULL DEFAULT 0`, so the Zod `.default(0)` is redundant for that column. Similarly for `price`. But `MarketMetaSchema` fields — pivot of `v_market_meta` — CAN be null if an indicator key isn't in `computed_market_indicators` (e.g., FRED API failed).

**Recommend PARTIAL**: drop `.default(0)` on `FidelityTxnSchema.quantity` / `.price` (safe). Keep nullable on `MarketMetaSchema` (real case).

- LOC: ~2
- Risk: L for the partial
- Primary use case: N (validation is fast either way)

---

### [P09] Archive stale `docs/sync-design-audit-2026-04-12.md` — DOC ORG

**Verified.** The audit doc is the historical record for today's work; the plan doc (`plan-automation-readiness-2026-04-12.md`) is the live execution reference. Having both in active `docs/` creates ambiguity about which is source of truth.

**Change:** Move `sync-design-audit-2026-04-12.md` → `docs/archive/sync-design-audit-2026-04-12.md`. Add a one-line marker at top: "ARCHIVED: findings executed via PRs #109-#114."

- LOC: 0 (move, not delete)
- Risk: L
- Primary use case: N (cognitive overhead only)

---

### [P10] Stale section in `docs/todo-plan-2026-04.md` — DOC CLEANUP

**Partial verification.** The "Local DB rebuild" section (lines 115-136) is stale — today's rebuild happened, verify_vs_prod.py now exists (PR #110, contra the agent's claim that it doesn't). But the section still describes `verify_vs_prod.py` as "to be written" and lists manual steps now encoded in automation.

**Change:** Delete lines 115-136 + the "R2/R3/R4 big refactor candidates" section (lines 137-163) since R4 was done (package rename) and R2/R3 are deferred without commitment. Keep §0, §1 (status), §4 (not doing), §5-6 (dependency graph, open questions).

- LOC: ~50
- Risk: L
- Primary use case: N

---

### [P11] README TODOs contain completed items — DOC CLEANUP

**Verified.** `README.md:274-286` has `[x]` checkmarks on ~7 completed items (FRED, Robinhood, D1 migration, 401k QFX, econ dashboard, R2 drop). The `[ ]` items (Gmail, News aggregation, AI narrative) are the only active ones.

**Change:** Move completed items to a separate section "## Completed (2026-Q2)" or just delete them. Keep only active `[ ]` items.

- LOC: ~7 if deleted, ~0 if reorganized
- Risk: L
- Primary use case: N

---

### [P12] Unused DB views in `worker/schema.sql` — NEEDS VERIFICATION, DEFERRED

**Agent claim UNVERIFIED.** Agent said `v_market_indicators` and `v_econ_series` are never queried. Spot-checked `worker/src/index.ts` — it doesn't reference these by name, but `v_market_meta` and `v_econ_series_grouped` do subsume their data.

**Need to verify**: grep the entire codebase including any scripts, wranger config, or admin utilities before deleting. Views don't cost runtime (materialized only on SELECT) but pollute schema.

**Recommend DEFER to a dedicated micro-audit PR**. Risk of silent breakage is real if anything does query these.

- LOC: ~3 each
- Risk: M (unverified)
- Primary use case: N
- **Verdict: DEFER**

---

### [P13] Agent false positives (recording for memory)

Findings that didn't survive vetting:
- `_load_config` supposedly unused → actually called at line 503 ✗
- `--positions` already removed by PR #113 → wrong, it's still at line 92 (it IS a real finding, but I initially believed the agent's assumption) — now tracked as [P01]
- `DailyTicker.costBasis/gainLoss/gainLossPct` "never referenced" → too risky to verify via grep alone; these are used by the ticker performance table. Skip.
- `cn()` util removal → it's the standard shadcn pattern; minor import cost, high idiom value. Skip.
- Dead `UTC` import in `allocation.py` → **needs verification** (agent says unused; import is for `datetime.UTC` which might still be needed). Will check during P01+P04 PR.

---

## Recommended PR split

Respect file-disjointness with in-flight PR #114 (touches `CLAUDE.md`, `README.md`, `docs/ARCHITECTURE.md`, `verify_positions.py`, PS1 wrapper). 

**Can dispatch NOW (no overlap with PR #114):**
- **PR S1 — Pipeline simplifications**: P01 (--positions), P02 (dedupe qj parse), P04 (inline categorize), P05 (remove _to_float), P06 (pyproject name). Net ~-55 LOC.
  - Touches: `pipeline/etl/allocation.py`, `pipeline/etl/timemachine.py`, `pipeline/etl/ingest/qianji_db.py`, `pipeline/scripts/build_timemachine_db.py`, `pipeline/scripts/verify_qianji.py`, `pipeline/pyproject.toml`.
- **PR S2 — CI + Worker schema cleanup**: P03 (CI dedupe next build), P08-partial (Zod FidelityTxn defaults). Net ~-3 LOC + faster CI.
  - Touches: `.github/workflows/ci.yml`, `src/lib/schemas/timeline.ts` (or wherever FidelityTxnSchema lives).

**Hold until PR #114 merges:**
- **PR S3 — Docs consolidation**: P09 (archive audit), P10 (todo-plan cleanup), P11 (README TODOs). Net ~-60 LOC.
  - Touches: `docs/`, `README.md`. Conflicts with #114's README/ARCHITECTURE/CLAUDE edits.

**Deferred (separate micro-audit):**
- P12 (unused views) — needs full codebase grep + wrangler history check before deletion.
- P07 (Worker helper inlining) — SKIP, clarity > LOC.

---

## Total estimated LOC reduction

- PR S1: **~55 LOC** (plus clearer allocation + one-source-of-truth Qianji parse)
- PR S2: **~3 LOC** + ~90s faster CI
- PR S3 (after #114): **~60 LOC** doc cleanup
- **Total: ~120 LOC** and one wall-clock-minute saved per CI run.

---

## Files cited

- `pipeline/scripts/build_timemachine_db.py:92, 114, 119-121, 368-390, 503`
- `pipeline/etl/allocation.py:12, 14, 64-91, 192, 226`
- `pipeline/etl/timemachine.py:361-380, 424`
- `pipeline/scripts/verify_qianji.py:14-29, 59`
- `pipeline/etl/ingest/qianji_db.py:_parse_amount`
- `pipeline/pyproject.toml:2`
- `.github/workflows/ci.yml:41, 75`
- `docs/sync-design-audit-2026-04-12.md` (whole file — move)
- `docs/todo-plan-2026-04.md:115-163`
- `README.md:274-286`
- `src/lib/schemas/timeline.ts` (FidelityTxnSchema)
