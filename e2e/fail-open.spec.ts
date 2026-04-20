import { test, expect } from "@playwright/test";

// ── /timeline error-card scenarios ───────────────────────────────────────
// When the Worker reports an optional section failure (market/holdings/txns),
// the page must still render AND the affected section must show an explicit
// error card. See docs/archive/code-quality-review-2026-04.md finding #5.

test.describe("Timeline fail-open", () => {
  test("renders error card when market data fails", async ({ page }) => {
    // Intercept /timeline and rewrite market to null + errors.market
    await page.route("**/timeline*", async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      await route.fulfill({
        response: res,
        json: { ...body, market: null, errors: { market: "indices: db timeout" } },
      });
    });

    await page.goto("/finance");

    // Whole dashboard still renders.
    await expect(page.getByTestId("page-title")).toBeVisible({ timeout: 10_000 });

    // Market panel shows the explicit error card with the message.
    const marketError = page.getByTestId("market-error");
    await expect(marketError).toBeVisible();
    await expect(marketError).toContainText(/market data failed to load/i);
    await expect(marketError).toContainText(/db timeout/i);

    // Other panels are unaffected — cashflow and investment-activity still attached.
    await expect(page.locator("#cashflow")).toBeAttached();
    await expect(page.locator("#investment-activity")).toBeAttached();
  });

  test("renders normally when errors is empty (happy path)", async ({ page }) => {
    await page.goto("/finance");
    await expect(page.getByTestId("page-title")).toBeVisible({ timeout: 10_000 });
    // Market section renders its data, not the error card.
    await expect(page.getByTestId("market-error")).toHaveCount(0);
    await expect(page.getByText("S&P 500").first()).toBeVisible();
  });
});
