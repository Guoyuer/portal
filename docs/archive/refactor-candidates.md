# Refactor Candidates — Deep Analysis

**Status: ALL COMPLETED** (PR #86)

6 items deferred from the initial code quality review, then analyzed and executed.

---

## 1. TickerRow / RestTickerRow unification

**File:** `src/components/finance/shared.tsx` lines 121-201

**What's duplicated:** Both components accept the same 7 props (`symbol`, `count`, `total`, `expanded`, `onToggle`, `startDate`, `endDate`), render the same expand arrow + 3 columns + conditional `TickerChart`. The only difference:

| Aspect | TickerRow | RestTickerRow |
|--------|-----------|---------------|
| Row element | `<TableRow>` (shadcn) | `<tr>` (plain HTML) |
| Cell element | `<TableCell>` (shadcn) | `<td>` (plain HTML) |
| Text style | Default foreground | `text-muted-foreground` |
| Expanded chart cell | `<TableCell colSpan={3}>` | `<td colSpan={3}>` |

**Why the split exists:** `TickerRow` lives inside a shadcn `<Table>` / `<TableBody>`. `RestTickerRow` lives inside a plain `<table>` nested within a `<details>` collapsible. Shadcn Table components render as plain HTML elements but add specific class names.

**Proposed fix:** Single component with a `plain?: boolean` prop that switches between `<TableRow>`/`<TableCell>` and `<tr>`/`<td>`, plus toggles the muted text class.

**Blast radius:** `shared.tsx` only — both components are private (not exported).

**Risk:** LOW — the shadcn `TableRow`/`TableCell` render as `<tr>`/`<td>` with extra Tailwind classes. A `plain` prop that skips those classes preserves identical DOM structure.

**Verdict:** Worth doing. Saves ~40 lines of pure duplication within one file.

---

## 2. MetricCards prop consolidation

**File:** `src/components/finance/metric-cards.tsx` lines 103-125, called from `src/app/finance/page.tsx`

**Current signature (10 props):**
```tsx
MetricCards({
  total,           // from tl.allocation.total
  netWorth,        // from tl.allocation.netWorth
  categories,      // from tl.allocation.categories
  tickers,         // from tl.allocation.tickers
  savingsRate,     // from tl.cashflow
  takehomeSavingsRate, // from tl.cashflow
  goal,            // constant GOAL
  goalPct,         // derived from tl.allocation.total / GOAL
  allocationOpen,  // local state
  onAllocationToggle, // local callback
})
```

**Proposed fix:** Pass `allocation: AllocationResponse` directly. `goalPct` is derivable inside the component (`allocation.total / goal * 100`). This reduces 10 props to 7:
```tsx
MetricCards({
  allocation,
  savingsRate,
  takehomeSavingsRate,
  goal,
  allocationOpen,
  onAllocationToggle,
})
```

**Blast radius:** 2 files — `metric-cards.tsx` (component) and `page.tsx` (call site). One call site.

**Risk:** LOW — pure API surface change, no logic change. The component already destructures immediately.

**Verdict:** Worth doing. Reduces prop drilling and eliminates a derived value (`goalPct`) at the call site.

---

## 3. Python N+1 queries in precompute

**File:** `pipeline/generate_asset_snapshot/precompute.py`

**Problem 1 — `precompute_holdings_detail` (lines ~207-210):** For each held ticker (10-20 symbols), issues a separate `SELECT close FROM daily_close WHERE symbol = ?` query. Total: 10-20 queries.

**Proposed fix:** Single batch query:
```python
symbols_csv = ",".join(f"'{s}'" for s in real_tickers)
rows = conn.execute(
    f"SELECT symbol, close FROM daily_close WHERE symbol IN ({symbols_csv}) ORDER BY symbol, date"
).fetchall()
# Group by symbol in Python
```

**Problem 2 — `_compute_index_row` (lines ~62-66):** Called once per index ticker (4 total: ^GSPC, ^NDX, ^VIX, ^DXY). Each issues its own SELECT. Same batch fix applies.

**Blast radius:** `precompute.py` only — internal helper functions, not exported.

**Risk:** LOW — pure query optimization, identical result set. SQLite handles `IN (...)` efficiently.

**Performance impact:** Reduces ~20 DB round-trips to 2. On local SQLite this saves ~50ms; more significant if DB is remote.

**Verdict:** Worth doing. Clean win, zero risk.

---

## 4. StickyBrush duplicate state

**File:** `src/components/finance/timemachine.tsx` lines ~130-165

**Current state:** `StickyBrush` maintains its own `const [range, setRange] = useState(...)` initialized from `defaultStartIndex`/`defaultEndIndex`. On brush change, it calls both `setRange(...)` (local) and `onBrushChange(...)` (parent). The local state is used only to render date labels in the sticky bar.

**The parent already has this:** `use-bundle.ts` exposes `brushStart`/`brushEnd` which track the same values. `page.tsx` passes `tl.brushStart`/`tl.brushEnd` to `TimemachineChart` but not to `StickyBrush`.

**Proposed fix:** Add `brushStart`/`brushEnd` to `StickyBrush` props. Remove local `range` state. Use parent values for date label rendering.

**Blast radius:** 2 files — `timemachine.tsx` (component) and `page.tsx` (call site).

**Risk:** MEDIUM — Recharts Brush `onChange` is called during drag. Parent state update triggers re-render top-down. If this adds noticeable lag to the sticky brush during drag, the local state pattern would need to be restored. Needs manual testing with rapid brush drags.

**Verdict:** Worth trying, but test drag responsiveness. If laggy, revert.

---

## 5. Python _parse_float consolidation

**5 implementations of the same logic:**

| Location | Name | Differences |
|----------|------|-------------|
| `types.py:53` | `parse_currency` | Uses regex `r"[\$,\s]"`, handles `"--"` → 0.0 |
| `timemachine.py:61` | `_float` | Manual `.replace(",","").replace("$","")` |
| `db.py:199` | `_parse_float` | Same as `_float` |
| `fidelity_history.py:85` | `_parse_float` | Same as `_float` |
| `robinhood_history.py:40` | `_parse_float` | Same as `_float` |

**Are they identical?** The 4 private implementations (`_float`, 3x `_parse_float`) are functionally identical — strip `$` and `,`, return `float()` or `0.0`. `parse_currency` in `types.py` is slightly more robust: uses regex to strip any combo of `$`, `,`, whitespace, and explicitly handles `"--"` as 0.0.

**Proposed fix:** Rename `parse_currency` to `parse_float` in `types.py` (keep alias for backward compat). Replace all 4 private copies with imports from `types.py`.

**Blast radius:** 5 files. Each private function has 2-5 call sites within its file.

**Risk:** LOW-MEDIUM — The `parse_currency` regex version is a superset of the manual version. It handles everything the others handle plus `"--"` and whitespace. No existing call site passes `"--"`, so the extra handling is harmless. Run full pytest to verify.

**Verdict:** Worth doing. Eliminates 4 copies of the same function. The regex version in `types.py` is strictly better.

---

## 6. brushRef removal in use-bundle.ts

**File:** `src/lib/use-bundle.ts` lines 63-113

**Current pattern:** `brushRef` (useRef) shadows `fullRange` (useState). `onBrushChange` writes to `brushRef.current` first, then calls `setFullRange(...)` from the ref values.

**Why it exists:** When `onBrushChange` is called, it may receive only `startIndex` or only `endIndex`. It needs the "other" value from the previous state. The ref provides synchronous access to the latest value without closure staleness.

**Could use `setFullRange(prev => ...)` instead?** Yes:
```tsx
const onBrushChange = (state: { startIndex?: number; endIndex?: number }) => {
  setFullRange(prev => ({
    start: state.startIndex ?? prev.start,
    end: state.endIndex ?? prev.end,
  }));
};
```

**Risk:** LOW in theory — React's functional updater guarantees `prev` is always current. However, the `brushRef` pattern was likely chosen deliberately for Recharts compatibility. Recharts Brush calls `onChange` synchronously from mouse events, so stale closures aren't a real concern here.

**Blast radius:** 1 file, internal to hook.

**Verdict:** Worth doing but low priority. The code is correct as-is; the simplification saves 3 lines and removes one concept. Test brush drag after changing.

---

## Execution order (recommended)

| # | Item | Status |
|---|------|--------|
| 1 | TickerRow/RestTickerRow → single component with `plain` prop | Done |
| 2 | MetricCards: pass AllocationResponse directly (10 → 7 props) | Done |
| 3 | Batch N+1 queries in precompute.py | Done |
| 4 | StickyBrush: use parent brushStart/brushEnd | Done |
| 5 | Consolidate 4x `_parse_float` → `types.parse_float` | Done |
| 6 | Remove brushRef → `setFullRange(prev => ...)` | Done |
