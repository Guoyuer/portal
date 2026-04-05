# Economy Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an Economy dashboard page at `/econ` with macroeconomic indicators from FRED API — current snapshot values and 5-year historical trend charts with 1Y/3Y/5Y toggle.

**Architecture:** Pipeline fetches FRED data via `fredapi`, generates `econ.json` (snapshot + monthly time series), uploads to R2 alongside `latest.json`. Frontend fetches `econ.json`, validates with Zod, renders cards + Recharts line charts. Existing Yahoo Finance data (gold, DXY, USD/CNY) merged in.

**Tech Stack:** Python `fredapi`, Recharts (already used), Zod (already used), Next.js App Router

**Spec:** `docs/superpowers/specs/2026-04-05-economy-page-design.md`

---

## File Structure

### Pipeline (Python)
| File | Action | Responsibility |
|------|--------|---------------|
| `pipeline/generate_asset_snapshot/market/fred.py` | Create | FRED API data fetching — snapshot + 5Y series |
| `pipeline/tests/unit/market/test_fred.py` | Create | Unit tests for FRED fetcher |
| `pipeline/scripts/send_report.py` | Modify | Call FRED fetcher, write `econ.json` |
| `.github/workflows/report.yml` | Modify | Add `FRED_API_KEY` env, upload `econ.json` |

### Frontend (TypeScript)
| File | Action | Responsibility |
|------|--------|---------------|
| `src/lib/econ-schema.ts` | Create | Zod schema + inferred types for `econ.json` |
| `src/lib/config.ts` | Modify | Add `ECON_URL` constant |
| `src/components/econ/macro-cards.tsx` | Create | Snapshot value cards |
| `src/components/econ/time-series-chart.tsx` | Create | Generic multi-line Recharts chart |
| `src/app/econ/page.tsx` | Create | Page component — data loading, layout, range toggle |
| `src/components/layout/sidebar.tsx` | Modify | Enable Economy nav item |
| `e2e/econ.spec.ts` | Create | E2E tests |

---

### Task 1: FRED API Fetcher — Tests

**Files:**
- Create: `pipeline/tests/unit/market/test_fred.py`

- [ ] **Step 1: Create test file with unit tests**

```python
"""Tests for FRED API data fetcher."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


@pytest.fixture
def mock_fred():
    """Create a mock Fred client with sample data."""
    with patch("generate_asset_snapshot.market.fred.Fred") as MockFred:
        client = MagicMock()
        MockFred.return_value = client

        # Daily series: 5 data points across 2 months
        daily_index = pd.date_range("2025-12-28", periods=5, freq="B")
        client.get_series.side_effect = lambda series_id, **kw: {
            "DFF": pd.Series([4.33, 4.33, 4.33, 4.33, 4.33], index=daily_index),
            "DGS10": pd.Series([4.25, 4.26, 4.24, 4.25, 4.25], index=daily_index),
            "DGS2": pd.Series([3.95, 3.96, 3.94, 3.95, 3.95], index=daily_index),
            "VIXCLS": pd.Series([18.5, 19.0, 18.0, 18.5, 18.5], index=daily_index),
            "DCOILWTICO": pd.Series([72.5, 73.0, 72.0, 72.5, 72.5], index=daily_index),
            # Monthly series: CPI index values (not YoY yet)
            "CPIAUCSL": pd.Series(
                [300.0, 301.0, 302.0, 308.4],
                index=pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01", "2025-03-01"]),
            ),
            "CPILFESL": pd.Series(
                [310.0, 311.0, 312.0, 319.8],
                index=pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01", "2025-03-01"]),
            ),
            "UNRATE": pd.Series(
                [3.7, 3.8, 3.9, 4.1],
                index=pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01", "2025-03-01"]),
            ),
        }.get(series_id, pd.Series(dtype=float))

        yield client


def test_fetch_fred_data_returns_snapshot_and_series(mock_fred):
    from generate_asset_snapshot.market.fred import fetch_fred_data

    result = fetch_fred_data("test_key")
    assert result is not None
    assert "snapshot" in result
    assert "series" in result

    snap = result["snapshot"]
    assert snap["fedRate"] == pytest.approx(4.33, abs=0.01)
    assert snap["treasury10y"] == pytest.approx(4.25, abs=0.01)
    assert snap["treasury2y"] == pytest.approx(3.95, abs=0.01)
    assert snap["spread2s10s"] == pytest.approx(0.30, abs=0.01)
    assert snap["vix"] == pytest.approx(18.5, abs=0.5)
    assert snap["unemployment"] == pytest.approx(4.1, abs=0.1)


def test_fetch_fred_data_series_have_date_value_format(mock_fred):
    from generate_asset_snapshot.market.fred import fetch_fred_data

    result = fetch_fred_data("test_key")
    assert result is not None

    for key, series in result["series"].items():
        assert isinstance(series, list), f"series[{key}] is not a list"
        if len(series) > 0:
            assert "date" in series[0], f"series[{key}][0] missing 'date'"
            assert "value" in series[0], f"series[{key}][0] missing 'value'"
            assert isinstance(series[0]["date"], str)


def test_cpi_is_yoy_percent(mock_fred):
    from generate_asset_snapshot.market.fred import fetch_fred_data

    result = fetch_fred_data("test_key")
    assert result is not None
    # CPI YoY: (308.4 - 300.0) / 300.0 * 100 = 2.8%
    assert result["snapshot"]["cpi"] == pytest.approx(2.8, abs=0.1)


def test_fetch_fred_data_returns_none_without_key():
    from generate_asset_snapshot.market.fred import fetch_fred_data

    result = fetch_fred_data("")
    assert result is None


def test_fetch_fred_data_handles_api_failure():
    from generate_asset_snapshot.market.fred import fetch_fred_data

    with patch("generate_asset_snapshot.market.fred.Fred") as MockFred:
        MockFred.return_value.get_series.side_effect = Exception("API error")
        result = fetch_fred_data("test_key")
        # Should return partial result or None, never raise
        assert result is None or isinstance(result, dict)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd pipeline && python -m pytest tests/unit/market/test_fred.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'generate_asset_snapshot.market.fred'`

- [ ] **Step 3: Commit test file**

```bash
git add pipeline/tests/unit/market/test_fred.py
git commit -m "test: add unit tests for FRED API fetcher"
```

---

### Task 2: FRED API Fetcher — Implementation

**Files:**
- Create: `pipeline/generate_asset_snapshot/market/fred.py`

- [ ] **Step 1: Implement the FRED fetcher**

```python
"""FRED API data fetcher.

Fetches macro indicators (rates, inflation, employment, volatility)
with 5 years of monthly history. Never raises — returns None on failure.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd
from fredapi import Fred

log = logging.getLogger(__name__)

# ── Series configuration ─────────────────────────────────────────────────

DAILY_SERIES = {
    "fedRate": "DFF",
    "treasury10y": "DGS10",
    "treasury2y": "DGS2",
    "vix": "VIXCLS",
    "oilWti": "DCOILWTICO",
}

MONTHLY_SERIES = {
    "unemployment": "UNRATE",
}

CPI_SERIES = {
    "cpi": "CPIAUCSL",
    "coreCpi": "CPILFESL",
}

LOOKBACK_YEARS = 5


def fetch_fred_data(api_key: str) -> dict | None:
    """Fetch snapshot + 5Y monthly series from FRED.

    Returns ``{"snapshot": {...}, "series": {...}}`` or None on failure.
    """
    if not api_key:
        log.warning("FRED API key not set — skipping economic data")
        return None

    try:
        fred = Fred(api_key=api_key)
    except Exception:
        log.exception("Failed to initialize FRED client")
        return None

    start = datetime.now() - timedelta(days=LOOKBACK_YEARS * 365)
    snapshot: dict[str, float] = {}
    series: dict[str, list[dict[str, object]]] = {}

    # ── Daily series (resample to month-end) ──────────────────────────
    for key, series_id in DAILY_SERIES.items():
        try:
            raw = fred.get_series(series_id, observation_start=start)
            raw = raw.dropna()
            if raw.empty:
                continue
            monthly = raw.resample("ME").last().dropna()
            snapshot[key] = round(float(raw.iloc[-1]), 4)
            series[key] = [
                {"date": d.strftime("%Y-%m"), "value": round(float(v), 4)}
                for d, v in monthly.items()
            ]
        except Exception:
            log.warning("FRED: failed to fetch %s (%s)", key, series_id)

    # ── Monthly series (use as-is) ────────────────────────────────────
    for key, series_id in MONTHLY_SERIES.items():
        try:
            raw = fred.get_series(series_id, observation_start=start)
            raw = raw.dropna()
            if raw.empty:
                continue
            snapshot[key] = round(float(raw.iloc[-1]), 2)
            series[key] = [
                {"date": d.strftime("%Y-%m"), "value": round(float(v), 2)}
                for d, v in raw.items()
            ]
        except Exception:
            log.warning("FRED: failed to fetch %s (%s)", key, series_id)

    # ── CPI series (convert index → YoY %) ────────────────────────────
    for key, series_id in CPI_SERIES.items():
        try:
            raw = fred.get_series(series_id, observation_start=start - timedelta(days=400))
            raw = raw.dropna()
            if len(raw) < 13:
                continue
            yoy = raw.pct_change(periods=12) * 100
            yoy = yoy.dropna()
            # Filter to our lookback window
            yoy = yoy[yoy.index >= pd.Timestamp(start)]
            if yoy.empty:
                continue
            snapshot[key] = round(float(yoy.iloc[-1]), 2)
            series[key] = [
                {"date": d.strftime("%Y-%m"), "value": round(float(v), 2)}
                for d, v in yoy.items()
            ]
        except Exception:
            log.warning("FRED: failed to fetch %s (%s)", key, series_id)

    # ── Computed: 2s10s spread ─────────────────────────────────────────
    if "treasury10y" in snapshot and "treasury2y" in snapshot:
        snapshot["spread2s10s"] = round(snapshot["treasury10y"] - snapshot["treasury2y"], 4)

    if "treasury10y" in series and "treasury2y" in series:
        t10_map = {p["date"]: p["value"] for p in series["treasury10y"]}
        t2_map = {p["date"]: p["value"] for p in series["treasury2y"]}
        common_dates = sorted(set(t10_map) & set(t2_map))
        series["spread2s10s"] = [
            {"date": d, "value": round(t10_map[d] - t2_map[d], 4)}
            for d in common_dates
        ]

    if not snapshot:
        log.warning("FRED: no data fetched — all series failed")
        return None

    fetched = list(snapshot.keys())
    log.info("FRED: fetched %d indicators: %s", len(fetched), fetched)
    return {"snapshot": snapshot, "series": series}
```

- [ ] **Step 2: Create `__init__.py` if missing**

Run: `ls pipeline/tests/unit/market/__init__.py 2>/dev/null || touch pipeline/tests/unit/market/__init__.py`

- [ ] **Step 3: Run tests**

Run: `cd pipeline && python -m pytest tests/unit/market/test_fred.py -v`
Expected: All 5 tests PASS

- [ ] **Step 4: Commit**

```bash
git add pipeline/generate_asset_snapshot/market/fred.py pipeline/tests/unit/market/
git commit -m "feat: add FRED API data fetcher with 5Y history"
```

---

### Task 3: Pipeline Integration — Generate `econ.json`

**Files:**
- Modify: `pipeline/scripts/send_report.py` (after line 246, before the final print)

- [ ] **Step 1: Add FRED + econ.json generation to send_report.py**

Insert after the metadata/report.json write block (after line 241 `json_path.write_text(json_output)`), before the elapsed time print:

```python
    # ── Economic indicators (optional — requires FRED_API_KEY) ──────────
    import os

    from generate_asset_snapshot.market.fred import fetch_fred_data

    fred_key = os.environ.get("FRED_API_KEY", "")
    econ_data = fetch_fred_data(fred_key)
    if econ_data:
        # Merge Yahoo Finance data into snapshot (gold, DXY, USD/CNY already in market_data)
        if market_data:
            econ_data["snapshot"].setdefault("usdCny", market_data.usd_cny)
        econ_data["generatedAt"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        econ_json = json.dumps(econ_data, indent=2)
        econ_path = args.data_dir / "econ.json"
        econ_path.write_text(econ_json)
        _log(f"Econ data: {len(econ_data['snapshot'])} indicators → {econ_path}")
    else:
        _log("Econ data: skipped (FRED API key not set or all series failed)")
```

- [ ] **Step 2: Test locally**

Run: `cd pipeline && FRED_API_KEY=b72d37d7284b9ab97aef7c28168e1183 python -c "from generate_asset_snapshot.market.fred import fetch_fred_data; import json; d = fetch_fred_data('b72d37d7284b9ab97aef7c28168e1183'); print(json.dumps({k: len(v) if isinstance(v, list) else v for k, v in d['series'].items()}) if d else 'None')"`
Expected: Output showing series names with point counts (e.g., `{"fedRate": 60, "treasury10y": 60, ...}`)

- [ ] **Step 3: Commit**

```bash
git add pipeline/scripts/send_report.py
git commit -m "feat: generate econ.json from FRED data in pipeline"
```

---

### Task 4: GitHub Actions — FRED_API_KEY + econ.json Upload

**Files:**
- Modify: `.github/workflows/report.yml`

- [ ] **Step 1: Add FRED_API_KEY env to generate step**

Change the "Generate report JSON" step (line 65-67):

```yaml
      - name: Generate report JSON
        if: steps.freshness.outputs.stale == 'false'
        env:
          FRED_API_KEY: ${{ secrets.FRED_API_KEY }}
        run: cd pipeline && python scripts/send_report.py --data-dir ../data
```

- [ ] **Step 2: Add econ.json upload to the upload step**

Change the "Upload JSON to R2" step (lines 69-78) — add econ.json upload after existing lines:

```yaml
      - name: Upload JSON to R2
        if: steps.freshness.outputs.stale == 'false'
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
          CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
        run: |
          npx wrangler r2 object put asset-snapshot-data/reports/latest.json \
            --file ./data/report.json --remote
          npx wrangler r2 object put asset-snapshot-data/data/net_worth_history.json \
            --file ./data/net_worth_history.json --remote
          if [ -f ./data/econ.json ]; then
            npx wrangler r2 object put asset-snapshot-data/reports/econ.json \
              --file ./data/econ.json --remote
            echo "Uploaded econ.json"
          fi
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/report.yml
git commit -m "ci: add FRED_API_KEY env and econ.json upload"
```

---

### Task 5: Frontend Schema + Config

**Files:**
- Create: `src/lib/econ-schema.ts`
- Modify: `src/lib/config.ts`

- [ ] **Step 1: Create Zod schema for econ.json**

```typescript
// ── Zod schema for economic indicators data (econ.json) ──────────────

import { z } from "zod";

const EconPointSchema = z.object({
  date: z.string(),
  value: z.number(),
});

const EconSnapshotSchema = z.object({
  fedRate: z.number().optional(),
  treasury10y: z.number().optional(),
  treasury2y: z.number().optional(),
  spread2s10s: z.number().optional(),
  cpi: z.number().optional(),
  coreCpi: z.number().optional(),
  unemployment: z.number().optional(),
  vix: z.number().optional(),
  dxy: z.number().optional(),
  oilWti: z.number().optional(),
  goldPrice: z.number().optional(),
  usdCny: z.number().optional(),
});

export const EconDataSchema = z.object({
  generatedAt: z.string(),
  snapshot: EconSnapshotSchema,
  series: z.record(z.string(), z.array(EconPointSchema)).default({}),
});

export type EconPoint = z.infer<typeof EconPointSchema>;
export type EconSnapshot = z.infer<typeof EconSnapshotSchema>;
export type EconData = z.infer<typeof EconDataSchema>;
```

- [ ] **Step 2: Add ECON_URL to config.ts**

Add after the existing `REPORT_URL` line in `src/lib/config.ts`:

```typescript
export const ECON_URL = R2_PUBLIC_URL ? `${R2_PUBLIC_URL}/reports/econ.json` : "";
```

- [ ] **Step 3: Verify build**

Run: `npx next build`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
git add src/lib/econ-schema.ts src/lib/config.ts
git commit -m "feat: add econ.json Zod schema and ECON_URL config"
```

---

### Task 6: Macro Cards Component

**Files:**
- Create: `src/components/econ/macro-cards.tsx`

- [ ] **Step 1: Create the component**

```tsx
import type { EconSnapshot } from "@/lib/econ-schema";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const INDICATORS: { key: keyof EconSnapshot; label: string; format: (v: number) => string }[] = [
  { key: "fedRate", label: "Fed Rate", format: (v) => `${v.toFixed(2)}%` },
  { key: "treasury10y", label: "10Y Treasury", format: (v) => `${v.toFixed(2)}%` },
  { key: "spread2s10s", label: "2s10s Spread", format: (v) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(0)} bps` },
  { key: "cpi", label: "CPI (YoY)", format: (v) => `${v.toFixed(1)}%` },
  { key: "unemployment", label: "Unemployment", format: (v) => `${v.toFixed(1)}%` },
  { key: "vix", label: "VIX", format: (v) => v.toFixed(1) },
  { key: "dxy", label: "DXY", format: (v) => v.toFixed(1) },
  { key: "oilWti", label: "Oil (WTI)", format: (v) => `$${v.toFixed(0)}` },
  { key: "usdCny", label: "USD/CNY", format: (v) => v.toFixed(4) },
];

export function MacroCards({ snapshot }: { snapshot: EconSnapshot }) {
  const visible = INDICATORS.filter((ind) => snapshot[ind.key] != null);

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
      {visible.map((ind) => (
        <Card key={ind.key}>
          <CardHeader className="pb-1">
            <CardTitle className="text-xs text-muted-foreground">{ind.label}</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-lg font-bold">{ind.format(snapshot[ind.key]!)}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Verify build**

Run: `npx next build`
Expected: Build succeeds

- [ ] **Step 3: Commit**

```bash
git add src/components/econ/macro-cards.tsx
git commit -m "feat: add MacroCards component for economy page"
```

---

### Task 7: Time Series Chart Component

**Files:**
- Create: `src/components/econ/time-series-chart.tsx`

- [ ] **Step 1: Create the generic chart component**

```tsx
"use client";

import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { EconPoint } from "@/lib/econ-schema";

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function fmtMonth(d: string) {
  const idx = parseInt(d.slice(5, 7), 10) - 1;
  const year = d.slice(2, 4);
  return `${MONTH_NAMES[idx] ?? d} ${year}`;
}

function useIsDark() {
  const [isDark, setIsDark] = useState(false);
  useEffect(() => {
    const check = () => setIsDark(document.documentElement.classList.contains("dark"));
    check();
    const observer = new MutationObserver(check);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return isDark;
}

export interface LineConfig {
  dataKey: string;
  label: string;
  color: string;
  formatter?: (v: number) => string;
}

interface TimeSeriesChartProps {
  title: string;
  lines: LineConfig[];
  data: Record<string, EconPoint[]>;
  height?: number;
}

export function TimeSeriesChart({ title, lines, data, height = 280 }: TimeSeriesChartProps) {
  const isDark = useIsDark();

  // Merge all series into unified date-keyed rows
  const dateMap = new Map<string, Record<string, number>>();
  for (const line of lines) {
    const points = data[line.dataKey] ?? [];
    for (const p of points) {
      const row = dateMap.get(p.date) ?? {};
      row[line.dataKey] = p.value;
      dateMap.set(p.date, row);
    }
  }

  const merged = Array.from(dateMap.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, values]) => ({ date, ...values }));

  if (merged.length === 0) return null;

  return (
    <div>
      <h3 className="font-semibold mb-3">{title}</h3>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={merged} margin={{ top: 5, right: 20, left: 10, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={isDark ? "#334155" : "#e5e7eb"} />
          <XAxis
            dataKey="date"
            tickFormatter={fmtMonth}
            fontSize={11}
            tick={{ fill: "#9ca3af" }}
            interval="preserveStartEnd"
          />
          <YAxis
            fontSize={11}
            tick={{ fill: "#9ca3af" }}
            width={50}
            tickFormatter={lines[0]?.formatter ?? String}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: isDark ? "#1e293b" : "#fff",
              border: `1px solid ${isDark ? "#334155" : "#e5e7eb"}`,
              borderRadius: "8px",
              padding: "8px 12px",
            }}
            labelFormatter={fmtMonth}
            formatter={(value: number, name: string) => {
              const line = lines.find((l) => l.dataKey === name);
              return [line?.formatter ? line.formatter(value) : value.toFixed(2), line?.label ?? name];
            }}
          />
          {lines.length > 1 && <Legend />}
          {lines.map((line) => (
            <Line
              key={line.dataKey}
              dataKey={line.dataKey}
              name={line.label}
              stroke={line.color}
              strokeWidth={2}
              dot={false}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
```

- [ ] **Step 2: Verify build**

Run: `npx next build`
Expected: Build succeeds

- [ ] **Step 3: Commit**

```bash
git add src/components/econ/time-series-chart.tsx
git commit -m "feat: add TimeSeriesChart component for economy page"
```

---

### Task 8: Economy Page

**Files:**
- Create: `src/app/econ/page.tsx`

- [ ] **Step 1: Create the page component**

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { ECON_URL } from "@/lib/config";
import { EconDataSchema, type EconData, type EconPoint } from "@/lib/econ-schema";
import { Button } from "@/components/ui/button";
import { SectionHeader, SectionBody } from "@/components/finance/shared";
import { MacroCards } from "@/components/econ/macro-cards";
import { TimeSeriesChart, type LineConfig } from "@/components/econ/time-series-chart";
import { BackToTop } from "@/components/layout/back-to-top";

type Range = "1Y" | "3Y" | "5Y";

const RANGE_MONTHS: Record<Range, number> = { "1Y": 12, "3Y": 36, "5Y": 60 };

function filterSeries(series: Record<string, EconPoint[]>, months: number): Record<string, EconPoint[]> {
  const cutoff = new Date();
  cutoff.setMonth(cutoff.getMonth() - months);
  const cutoffStr = cutoff.toISOString().slice(0, 7);

  const result: Record<string, EconPoint[]> = {};
  for (const [key, points] of Object.entries(series)) {
    result[key] = points.filter((p) => p.date >= cutoffStr);
  }
  return result;
}

const RATE_LINES: LineConfig[] = [
  { dataKey: "fedRate", label: "Fed Rate", color: "#2563eb", formatter: (v) => `${v.toFixed(2)}%` },
  { dataKey: "treasury10y", label: "10Y Treasury", color: "#7c3aed", formatter: (v) => `${v.toFixed(2)}%` },
  { dataKey: "treasury2y", label: "2Y Treasury", color: "#f59e0b", formatter: (v) => `${v.toFixed(2)}%` },
];

const SPREAD_LINES: LineConfig[] = [
  { dataKey: "spread2s10s", label: "2s10s Spread", color: "#ef4444", formatter: (v) => `${(v * 100).toFixed(0)} bps` },
];

const INFLATION_LINES: LineConfig[] = [
  { dataKey: "cpi", label: "CPI (YoY)", color: "#ef4444", formatter: (v) => `${v.toFixed(1)}%` },
  { dataKey: "coreCpi", label: "Core CPI (YoY)", color: "#f59e0b", formatter: (v) => `${v.toFixed(1)}%` },
];

const UNEMPLOYMENT_LINES: LineConfig[] = [
  { dataKey: "unemployment", label: "Unemployment Rate", color: "#2563eb", formatter: (v) => `${v.toFixed(1)}%` },
];

const VIX_LINES: LineConfig[] = [
  { dataKey: "vix", label: "VIX", color: "#ef4444", formatter: (v) => v.toFixed(1) },
];

const OIL_LINES: LineConfig[] = [
  { dataKey: "oilWti", label: "WTI Crude", color: "#10b981", formatter: (v) => `$${v.toFixed(0)}` },
];

export default function EconPage() {
  const [data, setData] = useState<EconData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState<Range>("3Y");

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(ECON_URL, { cache: "no-store" });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const json = await res.json();
      const parsed = EconDataSchema.safeParse(json);
      if (!parsed.success) {
        console.error("Econ validation failed:", parsed.error.issues);
        throw new Error(`Invalid econ data: ${parsed.error.issues[0]?.message ?? "schema mismatch"}`);
      }
      setData(parsed.data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load economic data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  if (loading) {
    return (
      <div className="max-w-5xl mx-auto py-20 text-center text-muted-foreground">
        Loading economic data...
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="max-w-5xl mx-auto py-20 text-center">
        <p className="text-red-500 mb-4">{error ?? "No data"}</p>
        <Button onClick={fetchData} variant="outline">Retry</Button>
      </div>
    );
  }

  const filtered = filterSeries(data.series, RANGE_MONTHS[range]);

  return (
    <div className="max-w-5xl mx-auto space-y-10">
      {/* Header */}
      <div className="flex items-start sm:items-center justify-between gap-2">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight">Economy Dashboard</h1>
          <p className="text-xs text-muted-foreground mt-1">
            Updated: {data.generatedAt.replace("T", " ").replace("Z", " UTC")}
          </p>
        </div>
        <Button onClick={fetchData} variant="outline" size="sm" disabled={loading} className="flex-shrink-0">
          {loading ? "Loading..." : "Reload"}
        </Button>
      </div>

      {/* Range toggle */}
      <div className="flex gap-1 bg-muted rounded-lg p-1 w-fit">
        {(["1Y", "3Y", "5Y"] as Range[]).map((r) => (
          <button
            key={r}
            onClick={() => setRange(r)}
            className={`px-3 py-1 rounded-md text-sm font-medium transition-colors ${
              range === r
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {r}
          </button>
        ))}
      </div>

      {/* Macro overview */}
      <MacroCards snapshot={data.snapshot} />

      {/* Interest Rates */}
      <section>
        <SectionHeader>Interest Rates</SectionHeader>
        <SectionBody>
          <TimeSeriesChart title="Fed Rate & Treasuries" lines={RATE_LINES} data={filtered} />
          <div className="mt-6 pt-6 border-t border-border">
            <TimeSeriesChart title="Yield Curve Spread (10Y − 2Y)" lines={SPREAD_LINES} data={filtered} height={200} />
          </div>
        </SectionBody>
      </section>

      {/* Inflation */}
      <section>
        <SectionHeader>Inflation</SectionHeader>
        <SectionBody>
          <TimeSeriesChart title="Consumer Price Index (Year-over-Year)" lines={INFLATION_LINES} data={filtered} />
        </SectionBody>
      </section>

      {/* Labor Market */}
      <section>
        <SectionHeader>Labor Market</SectionHeader>
        <SectionBody>
          <TimeSeriesChart title="Unemployment Rate" lines={UNEMPLOYMENT_LINES} data={filtered} />
        </SectionBody>
      </section>

      {/* Market Sentiment */}
      <section>
        <SectionHeader>Market Sentiment</SectionHeader>
        <SectionBody>
          <TimeSeriesChart title="VIX (Volatility Index)" lines={VIX_LINES} data={filtered} />
        </SectionBody>
      </section>

      {/* Commodities */}
      <section>
        <SectionHeader>Commodities</SectionHeader>
        <SectionBody>
          <TimeSeriesChart title="WTI Crude Oil" lines={OIL_LINES} data={filtered} />
        </SectionBody>
      </section>

      <BackToTop />
    </div>
  );
}
```

- [ ] **Step 2: Verify build**

Run: `npx next build`
Expected: Build succeeds, `/econ` route appears

- [ ] **Step 3: Commit**

```bash
git add src/app/econ/page.tsx
git commit -m "feat: add Economy dashboard page with charts and range toggle"
```

---

### Task 9: Enable Sidebar + E2E Tests

**Files:**
- Modify: `src/components/layout/sidebar.tsx` (line 76)
- Create: `e2e/econ.spec.ts`

- [ ] **Step 1: Enable Economy in sidebar**

In `src/components/layout/sidebar.tsx`, change line 76:

```typescript
    comingSoon: false,
```

- [ ] **Step 2: Create E2E tests**

```typescript
import { test, expect } from "@playwright/test";

test.describe("Economy Dashboard", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/econ");
  });

  test("renders page title", async ({ page }) => {
    await expect(page.locator("h1")).toContainText("Economy Dashboard");
  });

  test("shows loading state or data", async ({ page }) => {
    // Either loading or loaded with content
    const hasContent = await page.getByText("Economy Dashboard").isVisible();
    expect(hasContent).toBe(true);
  });

  test("sidebar economy link is active", async ({ page }) => {
    const sidebar = page.locator("aside").first();
    const econLink = sidebar.locator("a").filter({ hasText: "Economy" });
    await expect(econLink).toBeVisible();
    // Should not have "soon" badge
    await expect(econLink.locator("text=soon")).not.toBeVisible();
  });

  test("navigates from finance to economy", async ({ page }) => {
    await page.goto("/finance");
    await page.getByText("Portfolio Snapshot").waitFor({ timeout: 5000 });
    const sidebar = page.locator("aside").first();
    await sidebar.locator("a").filter({ hasText: "Economy" }).click();
    await expect(page).toHaveURL(/\/econ/);
  });
});
```

- [ ] **Step 3: Verify build + run E2E**

Run: `npx next build && npx playwright test e2e/econ.spec.ts`
Expected: Build succeeds, basic E2E tests pass (data may show error state if `econ.json` not on R2 yet — that's ok, the tests check structure not data)

- [ ] **Step 4: Commit**

```bash
git add src/components/layout/sidebar.tsx e2e/econ.spec.ts
git commit -m "feat: enable Economy nav + add E2E tests"
```

---

### Task 10: Update README TODO

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update TODO list**

Replace the economy-related TODO items:

```markdown
- [x] Economic indicators dashboard — FRED time series charts
- [x] FRED API integration — populate macro indicators in Economy page
- [ ] AI-generated macro narrative — LLM summarizing economic conditions and cycle position
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: update TODO — economy page done, add AI narrative TODO"
```
