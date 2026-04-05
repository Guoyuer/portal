# Economy Page — Design Spec

## Overview

Add an Economy dashboard page (`/econ`) that displays macroeconomic indicators with current snapshot values and 5-year historical trend charts. Data sourced from FRED API (pipeline) and Yahoo Finance (existing). Users can toggle between 1Y / 3Y / 5Y time ranges.

Future: AI-generated narrative summarizing macro conditions and cycles (tracked as TODO, not in this spec).

## Data Source: FRED API

API key: configured via `FRED_API_KEY` environment variable (GitHub Actions secret).

### Series to Fetch

| Indicator | FRED Series ID | Frequency | Transform |
|-----------|---------------|-----------|-----------|
| Fed Funds Rate | DFF | Daily | Month-end value |
| 10Y Treasury | DGS10 | Daily | Month-end value |
| 2Y Treasury | DGS2 | Daily | Month-end value |
| 2s10s Spread | (computed) | — | DGS10 − DGS2 |
| CPI (YoY%) | CPIAUCSL | Monthly | YoY % change |
| Core CPI (YoY%) | CPILFESL | Monthly | YoY % change |
| Unemployment | UNRATE | Monthly | Raw value |
| VIX | VIXCLS | Daily | Month-end value |
| Oil WTI | DCOILWTICO | Daily | Month-end value |

Gold price, DXY, USD/CNY: sourced from Yahoo Finance (existing `build_market_data()`).

All series: fetch 5 years of history. Pipeline stores full 5Y; frontend truncates to user-selected range.

## Data File: `econ.json`

Stored at `asset-snapshot-data/reports/econ.json` on R2, alongside `latest.json`.

```json
{
  "generatedAt": "2026-04-05T13:00:00Z",
  "snapshot": {
    "fedRate": 4.33,
    "treasury10y": 4.25,
    "treasury2y": 3.95,
    "spread2s10s": 0.30,
    "cpi": 2.8,
    "coreCpi": 3.1,
    "unemployment": 4.1,
    "vix": 18.5,
    "dxy": 104.2,
    "oilWti": 72.5,
    "goldPrice": 2650.0,
    "usdCny": 7.24
  },
  "series": {
    "fedRate": [{ "date": "2021-04", "value": 0.07 }, ...],
    "treasury10y": [{ "date": "2021-04", "value": 1.63 }, ...],
    "treasury2y": [...],
    "spread2s10s": [...],
    "cpi": [...],
    "coreCpi": [...],
    "unemployment": [...],
    "vix": [...],
    "oilWti": [...],
    "dxy": [...],
    "goldPrice": [...],
    "usdCny": [...]
  }
}
```

Each series entry: `{ "date": "YYYY-MM", "value": number }`. ~60 points per series (5 years monthly).

## Pipeline Changes

### New file: `pipeline/generate_asset_snapshot/market/fred.py`

Single public function:

```python
def fetch_fred_data(api_key: str) -> dict | None:
    """Fetch snapshot + 5Y monthly series from FRED.
    Returns None if API key missing or all requests fail.
    """
```

- Uses `fredapi.Fred` client
- Fetches each series with `fred.get_series()`
- Daily series: resample to month-end
- CPI/Core CPI: compute YoY % change from raw index
- 2s10s spread: computed from 10Y − 2Y
- Graceful degradation: if individual series fail, omit from result (don't block others)

### Integration: `pipeline/scripts/send_report.py`

After existing report generation:
1. Call `fetch_fred_data(os.environ.get("FRED_API_KEY", ""))`
2. Merge Yahoo Finance data (gold, DXY, USD/CNY) into snapshot + series
3. Write `econ.json` to data dir
4. Upload to R2 alongside `latest.json`

### GitHub Actions: `.github/workflows/report.yml`

Add `FRED_API_KEY: ${{ secrets.FRED_API_KEY }}` to the "Generate report JSON" step env.

Add upload step for `econ.json`.

## Frontend

### Schema: `src/lib/econ-schema.ts`

Zod schema for `econ.json` validation. Infers TypeScript types.

### Config: `src/lib/config.ts`

Add `ECON_URL` pointing to `${R2_PUBLIC_URL}/reports/econ.json`.

### Page: `src/app/econ/page.tsx`

Client component. Same data-loading pattern as Finance page (fetch → Zod parse → render).

Layout (top to bottom):
1. Page title + generation timestamp
2. Time range toggle: 1Y / 3Y / 5Y (default 3Y). State stored in `useState`, filters series data in-memory.
3. Macro Overview cards (snapshot values)
4. Interest Rates chart (Fed Rate + 10Y + 2Y + 2s10s spread)
5. Inflation chart (CPI + Core CPI)
6. Labor Market chart (Unemployment)
7. Market Sentiment chart (VIX)
8. Commodities & FX chart (Oil + Gold + USD/CNY — separate sub-charts due to different scales)

### Components

| File | Purpose |
|------|---------|
| `src/components/econ/macro-cards.tsx` | Snapshot value cards (grid layout) |
| `src/components/econ/time-series-chart.tsx` | Generic multi-line Recharts chart. Props: `title`, `series`, `lines` config. Handles axis formatting, tooltips, dark mode. |
| `src/app/econ/page.tsx` | Page assembly, data loading, range toggle state |

Reuse from finance: `SectionHeader`, `SectionBody`, `BackToTop`, `fmtPct`, `fmtCurrency`, `valueColor`.

### Sidebar

`src/components/layout/sidebar.tsx`: change Economy entry from `comingSoon: true` to `comingSoon: false`.

## Error Handling

- FRED API key missing → skip econ.json generation, log warning
- Individual series fetch fails → omit from result, don't block other series
- `econ.json` not on R2 → Economy page shows "No data available" with explanation
- Zod validation fails → same error state as Finance page

## Testing

### Pipeline
- Unit tests for `fred.py`: mock `fredapi.Fred`, verify snapshot extraction, YoY calculation, month-end resampling
- Integration test: verify `econ.json` structure matches expected schema

### Frontend
- E2E tests: page loads, cards render, charts render, time range toggle works
- Build verification: TypeScript compiles, no errors

## TODO (future, not this spec)

- [ ] AI-generated macro narrative (LLM API call summarizing current conditions and cycle position)
- [ ] Also populate `MarketData` fields in `latest.json` from FRED data (so Finance page's Market Context gets real macro indicators too)
