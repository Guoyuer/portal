# UI/UX Polish TODO

Based on visual review of the finance dashboard (April 2026).

---

## P0 — High impact, easy fixes

### 1. Hide empty macro indicators
**File:** `src/components/finance/market-context.tsx`
**Problem:** Market Context shows "Macro Indicators" section even when only USD/CNY has a value — Fed Rate, CPI, VIX etc. are all null from the pipeline (FRED API not integrated). Showing a grid with a single item looks broken.
**Fix:** The code already checks `if (m.fedRate != null)` per indicator, and wraps the grid in `indicators.length > 0`. Currently only USD/CNY shows up, which is fine — but if only 1-2 indicators exist, switch from 2-column grid to inline display. Or: just move USD/CNY into the index returns table as a row, and hide the "Macro Indicators" sub-section entirely until FRED is integrated.
**Effort:** 15 min

### 2. Upcoming Earnings — collapse after 5
**File:** `src/app/finance/page.tsx` (Holdings Detail section)
**Problem:** 15 upcoming earnings listed vertically with no folding. Inconsistent with other sections that collapse overflow (Cash Flow, Ticker tables).
**Fix:** Show first 5, use `<details>` for the rest, same pattern as `TickerTable` in `shared.tsx`.
**Effort:** 15 min

### 3. Savings Rate color threshold
**File:** `src/components/finance/metric-cards.tsx:37`
**Problem:** Savings Rate always shows `text-green-600` regardless of value. A 5% savings rate shouldn't look the same as 60%.
**Fix:** `>= 30%` green, `15-30%` yellow (`text-yellow-600`), `< 15%` red. Same for take-home rate.
**Effort:** 10 min

---

## P1 — Medium impact, moderate effort

### 4. Merge Holdings Detail + Gain/Loss
**Files:** `src/app/finance/page.tsx`, `src/components/finance/gain-loss.tsx`
**Problem:** Two separate sections both list individual stocks. Holdings Detail shows month return + vs 52W high. Gain/Loss shows cost basis + unrealized gain. User has to cross-reference.
**Fix:** Merge into a single "Holdings" section with a combined table: Ticker | Value | Month Return | Cost Basis | Gain/Loss | Gain % | vs 52W High. Top/Bottom performers can be a summary row above the full table. Keep upcoming earnings as a sub-section below.
**Effort:** 1-2 hours. Need to join data from `holdingsDetail.allStocks` with `equityCategories/nonEquityCategories` holdings (which have cost basis). Match by ticker.

### 5. Back to top button
**File:** `src/app/finance/page.tsx` or `src/components/layout/`
**Problem:** Page is very long (13+ sections). No way to jump back to top after scrolling.
**Fix:** Floating button in bottom-right corner, appears after scrolling past first screen. `onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}`. Use a simple up-arrow icon.
**Effort:** 30 min

### 6. Section navigation anchors
**File:** `src/app/finance/page.tsx`, sidebar or floating TOC
**Problem:** 13 sections, no way to jump to a specific one. User must scroll through everything.
**Fix:** Two options:
- **A. Sticky section tabs** below the header — a horizontal scroll of section names that auto-highlight as you scroll (IntersectionObserver). Click to jump.
- **B. Sidebar TOC** — add section links to the left sidebar under "Finance". Simpler but less discoverable.
Recommend A for desktop, B is fallback.
**Effort:** 2-3 hours for option A (IntersectionObserver + scroll-to + highlight state)

---

## P2 — Nice to have

### 7. Section header visual weight
**File:** `src/components/finance/shared.tsx:13-18`
**Problem:** Dark blue headers (`bg-[#16213e]`) blend together when scrolling fast. Hard to tell where one section ends and another begins.
**Fix:** Add more vertical spacing between sections (`space-y-10` instead of `space-y-8`), or add a subtle top-border/divider above each section header. Could also make headers sticky within their section for context while scrolling.
**Effort:** 15 min for spacing, 1 hour for sticky headers

### 8. Credit cards — collapse when trivial
**File:** `src/components/finance/balance-sheet.tsx:70-78`
**Problem:** Three credit cards with -$22, -$61, -$95 take up 3 rows. When total liability is < $500, this granularity adds noise.
**Fix:** If total liabilities < $500, show a single row "Credit Cards (3)" with the total. Expand on click. If > $500, show individual cards as today.
**Effort:** 20 min

### 9. Dark mode chart colors
**File:** `src/components/finance/charts.tsx`
**Problem:** Income (green) and Expenses (red) bars are dim on dark background. The area chart fill is also barely visible.
**Fix:** Use brighter variants in dark mode — `dark:fill-green-400` / `dark:fill-red-400`. Recharts doesn't support Tailwind classes directly, so need to detect dark mode via `useIsMobile`-style hook and swap color values.
**Effort:** 1 hour (need a `useIsDark()` hook + conditional colors in chart props)

### 10. Chart tooltip styling
**File:** `src/components/finance/charts.tsx`
**Problem:** Default Recharts tooltip is small and plain. On the Net Worth trend chart, it's easy to miss.
**Fix:** Custom tooltip component with larger font, card-style background, and formatted values. Recharts supports `<Tooltip content={<CustomTooltip />} />`.
**Effort:** 45 min

---

## Implementation order

```
P0 (quick wins, do together):    #1, #2, #3     → ~40 min
P1 (next sprint):                #5, #4, #6     → ~4 hours
P2 (when time permits):          #7, #8, #9, #10 → ~3 hours
```
