import { test, expect } from "@playwright/test";

// ── Real-worker smoke ────────────────────────────────────────────────────
// Runs under playwright.config.real.ts against a live `wrangler dev --local`
// seeded from L2 fixtures (pipeline/scripts/seed_local_d1_from_fixtures.sh).
//
// The existing mock-based specs (finance.spec.ts, fail-open.spec.ts, etc.)
// hard-code fixture-specific strings ("S&P 500", "Buys by Symbol", specific
// ticker names) that don't 1:1 match the L2 golden data, so we don't try to
// reuse them. Instead these three smoke assertions catch the bug class the
// mock API cannot: D1 schema drift, view column aliasing, worker code
// defects, Zod parse mismatches on real payloads.

test.describe("Real-worker smoke", () => {
  test("page renders the finance title against real worker", async ({ page }) => {
    await page.goto("/finance");
    await expect(page.getByTestId("page-title")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("h1")).toContainText("Dashboard for Yuer");
  });

  test("/timeline returns non-empty rows + expected top-level keys", async ({ request }) => {
    // Direct API assertion: the Worker parses D1 views, serializes to JSON,
    // matches the keys the frontend Zod schema expects. No mock indirection.
    const res = await request.get("http://localhost:8787/timeline");
    expect(res.status()).toBe(200);
    const body = await res.json();

    // Top-level keys the frontend assumes — a missing key would trip Zod.
    for (const key of ["daily", "dailyTickers", "fidelityTxns", "qianjiTxns", "categories", "errors"]) {
      expect(body, `missing ${key}`).toHaveProperty(key);
    }

    // L2 fixtures produce real rows — empty would mean sync never landed.
    expect(Array.isArray(body.daily)).toBe(true);
    expect(body.daily.length).toBeGreaterThan(0);
    expect(Array.isArray(body.categories)).toBe(true);
    expect(body.categories.length).toBeGreaterThan(0);

    // First daily row shape (camelCase — views must be aliasing correctly).
    const d0 = body.daily[0];
    expect(d0).toHaveProperty("date");
    expect(d0).toHaveProperty("total");
    expect(d0).toHaveProperty("usEquity");
  });

  test("net-worth number rendered from real data (has-recent-data)", async ({ page }) => {
    // Exercises the full path: wrangler → D1 view → JSON → Next static
    // bundle's Zod parse → component render. If any layer drifts, the tile
    // either fails to render or shows N/A, both of which this catches.
    await page.goto("/finance");
    const tile = page.getByTestId("net-worth-card");
    await expect(tile).toBeVisible({ timeout: 15_000 });
    // Dollar-formatted net worth — matches "$16,753" / "$16.7k" / "$412,883".
    await expect(tile.locator("text=/\\$[\\d,.]+k?/").first()).toBeVisible();
  });
});
