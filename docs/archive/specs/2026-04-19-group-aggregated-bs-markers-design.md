# Group-Aggregated B/S Markers — Design

**Date:** 2026-04-19
**Status:** Draft (awaiting user approval)
**Scope:** Frontend (holdings table + ticker dialog + marker rendering). No worker / D1 / pipeline changes.

---

## Problem

Ticker-level B/S markers are noisy because the user regularly rebalances between economically-equivalent tickers (e.g., sell QQQ, buy QQQM — both NASDAQ-100). Those trades show up as a big S on the QQQ chart and a big B on the QQQM chart, but the NASDAQ-100 exposure is unchanged. The markers misrepresent the user's actual decisions.

## Goal

Offer a **group view** that aggregates economically-equivalent tickers so B/S markers reflect actual net exposure change. Per-ticker view stays available for raw truth.

## Semantics (decided)

- **Primary:** exposure change. Group B/S fires when the user's exposure to a group *actually* changes.
- **Secondary:** cash flow. Trades within a group that net to ≈$0 cash are swaps → no marker.
- Partial swaps (e.g., sell $1000 SPY, buy $500 VOO) are normal; group view renders the net ($500 S). There is no "swap" marker kind — "swap" is just "net ≈ 0".

## Design

### Transaction taxonomy

Classify each transaction (data layer) into a `TxnType`:

| TxnType | Criterion | Per-ticker marker | Participates in group net? |
|---|---|---|---|
| `REAL` | Buy/sell with real cash flow | B (●) or S (◆) | ✅ Yes |
| `REINVEST` | Automatic dividend reinvest | Small dot (●, ~3px) | ❌ No |
| `SPLIT` | Stock split (`action_kind = DISTRIBUTION`, price = 0) | Hidden | ❌ No |
| `ROLLOVER` | Same-ticker cross-account transfer | Hidden | ❌ No |
| `OTHER` | Interest, foreign tax, etc. | Hidden | ❌ No |

Mapping from Fidelity `action_kind` → `TxnType` is implementation detail; spec guarantees the taxonomy shape.

**Note:** SPLIT is already hidden today because `compute.ts` only matches `actionType === "buy" | "sell"` and splits come through as `DISTRIBUTION`. No regression risk on the SPLIT path.

### Equivalence groups

Hand-maintained in `pipeline/config.json`:

```json
{
  "equivalent_groups": {
    "nasdaq_100": {
      "display": "NASDAQ 100",
      "tickers": ["QQQ", "QQQM", "401k tech"]
    },
    "sp500": {
      "display": "S&P 500",
      "tickers": ["VOO", "IVV", "SPY", "FXAIX", "401k sp500"]
    }
  }
}
```

- Key: stable snake-case identifier (`nasdaq_100`).
- `display`: human-readable name shown in the UI.
- `tickers`: equivalence-class members.
- Validation (ingest-time): a ticker appearing in two groups → hard error; fail the build.
- Tickers not listed in any group are implicitly solo (their own singleton "group" conceptually, but displayed as a plain row).

### Group-net algorithm

Clusters of REAL transactions within a group produce one marker per cluster.

```
for each group g:
    txns = REAL txns in g, sorted by date
    clusters = []
    for txn in txns:
        if clusters and (txn.date − clusters[-1].last_date) ≤ 2 days:
            clusters[-1].append(txn)        # extend existing cluster
        else:
            clusters.append([txn])          # start new cluster

    for cluster in clusters:
        net = Σ(|amount| if side=sell else −|amount|  for each txn in cluster)
        if |net| < $50:                     # exposure unchanged → swap noise
            skip                            # no marker
        elif net > 0:
            emit S marker at cluster[0].date, amount = net
        else:
            emit B marker at cluster[0].date, amount = |net|
```

- **Window: T+2 chaining.** Two txns within 2 calendar days join the same cluster; chains can extend (day 1 → day 3 → day 5 all in one cluster if each adjacent pair is within 2 days). Settles T+2 convention; weekend rollovers straddle OK.
- **Cross-account aggregation:** yes. The group is ticker-level; accounts don't enter the equivalence relation.
- **Threshold: $50.** Tolerates floating-point dust; catches any meaningful partial swap.
- **REINVEST / SPLIT / ROLLOVER / OTHER:** not in the cluster scan.
- **Marker anchor date:** earliest date in the cluster. Simple + stable.
- **Sign convention:** `amount` is absolute; `side ∈ {buy, sell}` from `actionType` (or `actionKind` post-classification). Net > 0 means sells exceeded buys (cash flowed in to the user) → S marker.

### Known limitation

Swaps that straddle > T+2 (e.g., sell Monday, buy 10 days later after cash sits in MM) do **not** pair. Group view will show a real S + real B separated in time. No workaround without cash-balance tracking; accept as V1 limitation.

---

## UI / UX

### Holdings table (the main entry point)

A single top-of-table toggle controls the view:

```
┌─────────────────────────────────────────────────────┐
│  Holdings            [ Group equivalent tickers ⚪ ] │
├─────────────────────────────────────────────────────┤
│  Ticker     Value     Cost     Gain/Loss    ...     │
│  ...                                                │
└─────────────────────────────────────────────────────┘
```

**Toggle behavior:**
- Default: **ON** (fresh page load starts in group view — solving the user's original pain).
- **No persistence.** In-memory state only; each session starts ON.
- Located top-right of the holdings table.
- Label: "Group equivalent tickers".

**When OFF (exactly today's behavior):**
- Ticker-first table, no groupings, every row is a ticker. No regression.

**When ON:**
- **Row types:**
  - *Group rows* (multi-member) — labeled by group `display` name. Shows aggregated value / cost / gain-loss. Left edge has ▸ chevron (collapsed) / ▾ (expanded).
  - *Solo-ticker rows* — tickers not in any group, rendered exactly like collapsed group rows (same height, same columns, same typography — **visually indistinguishable from a collapsed group**, no chevron).
- **Collapsed state:** solo row and collapsed group row are visually identical. Mental model: "every row is a position in the portfolio."
- **Expanded state:** click a group row's chevron (or the row itself) → child ticker rows appear indented (1.5 rem). No tree lines, no background shading — simple left-indent.
- **Sort:** default by market value DESC. Sort applies to top-level rows (groups + solos mixed). Within an expanded group, child tickers sort by value DESC.
- **Columns:** identical to today's ticker-level table (value, cost, gain/loss, %). Group rows show sums; group-level `% of portfolio` includes all members. No new columns.
- **Ticker chart (per-ticker dialog):** unchanged. Click any ticker (including an expanded child) → per-ticker dialog, raw REAL markers.

### Group dialog (new)

Opened by clicking a group row (when it has > 1 member). Single-member groups don't need a group dialog — clicking a solo row opens the existing per-ticker dialog.

**Layout (mirrors the existing ticker dialog):**
- **Header:** group `display` name, total value, total cost, total gain/loss, constituents list with weights (e.g., "QQQ 60% · QQQM 40%").
- **Chart:**
  - Y-axis: **total market value** ($). No price line concept at group level.
  - Cost-basis overlay line (same as ticker chart).
  - **Holding-period gradient background: dropped.** V1 shows no gradient. (No single holding period exists across members.)
  - B/S markers: computed via the group-net algorithm above.
  - Brush + date-range selector: same as ticker chart.

### Marker styling

| What | Visual |
|---|---|
| REAL buy | Solid ● filled with `BUY_COLOR`, white "B" inside (same as today) |
| REAL sell | Solid ◆ filled with `SELL_COLOR`, white "S" inside (same as today) |
| REINVEST | Tiny ● (~3px diameter), muted color (e.g., `BUY_COLOR` at 40% opacity). No letter, no cluster badge. Per-ticker view only. |
| SPLIT / ROLLOVER / OTHER | Not rendered |
| Group swap (|net| < $50) | Not rendered |

### Tooltips

**Group view marker hover:**
```
Net −$500 sell  (S&P 500)
  SPY   −$1000
  VOO   +$500
```
Breakdown by contributing ticker + signed amount. Breakdowns surface the swap structure, which is the main value of the group view.

**Per-ticker view marker hover:** unchanged (same as today).

### Interaction

- Group marker click: no-op (V1). Hover-reveal breakdown is enough. A future version could drill into constituent ticker charts.
- Per-ticker marker click: unchanged.

---

## Implementation anchors

Single PR, UIUX-scope only:

1. **Data layer (frontend):**
   - `src/lib/compute/compute.ts`: add `classifyTxn(txn) → TxnType` helper.
   - `src/lib/compute/compute.ts`: add `groupNetByDate(txns, groups, window) → Map<groupKey, Map<date, netEntry>>`.
   - Consumes the existing `TimelineData` bundle — no new network calls.
2. **Config:**
   - `pipeline/config.json` schema extension: `equivalent_groups`. Surfaced to frontend via the existing `/timeline` → `categories` delivery path (extend to include groups metadata).
   - Or: inline in a frontend constant. Decide in plan; UX-wise identical.
3. **Markers:**
   - `src/components/finance/ticker-markers.tsx`: extend `MarkerKind` to include `reinvest`.
   - Filter based on `TxnType` upstream of rendering.
4. **Holdings table:**
   - `src/components/finance/ticker-table.tsx`: add grouping toggle, group-row component, expand/collapse state.
5. **Group dialog:**
   - New `src/components/finance/group-dialog.tsx`. Mirror `ticker-dialog.tsx` structure; swap in group-aggregated data; skip holding-period gradient code path.
6. **Validation:**
   - Pipeline-side: `equivalent_groups` ticker-in-two-groups check in `etl/validate.py`.
7. **Tests:**
   - Unit: `classifyTxn`, `groupNetByDate` (window edge cases, partial swap, threshold).
   - Visual: at least one smoke e2e asserting group toggle switches the table shape.

No pipeline / worker / D1 changes needed. All aggregation is client-side.

---

## Out of scope (V1)

- Price-correlation auto-equivalence detection.
- Holding-period gradient in group dialog.
- Group marker drill-down / click behavior.
- Per-ticker swap-pair annotation (the per-ticker view stays raw).
- Persistence of the toggle state across sessions.
- Timemachine chart changes (stays category-level).
- Cross-day swap detection beyond T+2.
- Tax-loss-harvesting awareness (sold-at-loss swaps still read as swaps).

---

## Testing

- Unit tests for `classifyTxn` — one case per `TxnType`.
- Unit tests for `groupNetByDate`:
  - Same-day exact swap → no marker.
  - Same-day partial swap → marker = net.
  - T+1 swap (e.g., sell Monday, buy Tuesday) → pairs.
  - T+3 gap → does NOT pair (shows two markers).
  - REINVEST / SPLIT excluded from net.
  - Cross-account same-group swap pairs correctly.
  - Threshold edge: $49 net → no marker; $51 → marker.
- Vitest for table component: toggle ON/OFF renders different row sets; expand/collapse state.
- Playwright smoke: click the toggle, observe group rows appear.
- No golden-snapshot for marker positions (fragile); assert data shape instead.

---

## Open for implementation plan

Decisions below are intentionally deferred to the implementation plan, not the design:

- Where exactly `equivalent_groups` is exposed to the frontend (bundle vs. static import).
- Exact Fidelity `action_kind` → `TxnType` table (parse-time mapping).
- Whether to add a D1 view (rejected in design — keep client-side). Revisit only if bundle size regresses.
- Group dialog's chart primitive: reuse `ticker-chart-base.tsx` with an injected data source, or a separate `group-chart.tsx`?
