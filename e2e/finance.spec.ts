import { test, expect } from "@playwright/test";
import type { Locator, Page } from "@playwright/test";

function investmentActivity(page: Page): Locator {
  return page.locator("#investment-activity");
}

async function visibleActivityTable(page: Page): Promise<{ section: Locator; table: Locator }> {
  const section = investmentActivity(page);
  await expect(section).toBeVisible();
  await expect(section.getByText("Buys by Symbol")).toBeVisible();
  await expect(section.getByText("Dividends by Symbol")).toBeVisible();
  const table = section.locator("table").first();
  await expect(table).toBeVisible();
  return { section, table };
}

async function disableGroupedActivity(section: Locator): Promise<void> {
  await section.getByRole("checkbox", { name: /Group equivalent tickers/i }).uncheck();
}

async function expandActivityOverflow(section: Locator): Promise<void> {
  const summaries = section.locator("details summary");
  const count = await summaries.count();
  expect(count).toBeGreaterThan(0);
  for (let i = 0; i < count; i++) {
    await summaries.nth(i).click();
  }
}

test.describe("Finance Report", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/finance");
    await page.getByTestId("page-title").waitFor({ timeout: 10_000 });
  });

  test("renders core dashboard sections", async ({ page }) => {
    await expect(page.locator("h1")).toContainText("Dashboard for Yuer");
    const sidebar = page.locator("aside").first();
    await expect(sidebar.getByText("Portal")).toBeVisible();
    await expect(sidebar.getByText("Finance")).toBeVisible();

    await expect(page.getByTestId("net-worth-card")).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/Investment/).first()).toBeVisible();
    await expect(page.getByText(/\$\d+k/).first()).toBeVisible();
    await expect(page.getByTestId("goal-card")).toBeVisible();

    await page.getByTestId("net-worth-card").getByRole("button").click();
    await expect(page.getByRole("cell", { name: "US Equity", exact: true })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Non-US Equity" })).toBeVisible();
    await expect(page.locator(".recharts-pie")).toBeVisible();

    const cashflowSection = page.locator("#cashflow");
    await expect(cashflowSection.getByTestId("income-table")).toBeVisible();
    await expect(cashflowSection.getByTestId("income-table").getByRole("row", { name: /Total/ })).toBeVisible();
    await expect(cashflowSection.getByTestId("expense-table")).toBeVisible();
    await expect(cashflowSection.getByTestId("expense-table").getByRole("row", { name: /Total/ })).toBeVisible();
    const bars = cashflowSection.locator(".recharts-bar-rectangle");
    await expect(bars.first()).toBeVisible({ timeout: 5000 });

    const market = page.getByTestId("market-section");
    await expect(market.getByText("S&P 500")).toBeVisible();
    await expect(market.getByText("NASDAQ 100")).toBeVisible();
    await expect(market.getByTestId("market-error")).toHaveCount(0);

    const tmSection = page.locator("#timemachine");
    await expect(tmSection.locator(".recharts-area").first()).toBeVisible();
    await expect(tmSection.getByText("Net Savings")).toBeVisible();
    await expect(page.locator(".recharts-brush").first()).toBeVisible();
  });

  test("shows buys/dividends and collapsible overflow", async ({ page }) => {
    const { section } = await visibleActivityTable(page);
    const details = section.locator("details").filter({ hasText: /and \d+ more/ });
    await expect(details.first()).toBeVisible();
    await details.first().locator("summary").click();
    await expect(details.first().locator("tr").first()).toBeVisible();
  });

  test("ticker chart shows buy markers and avg cost line", async ({ page }) => {
    const { section, table: activityTable } = await visibleActivityTable(page);
    await disableGroupedActivity(section);
    const firstTicker = activityTable.locator("td.font-mono").first();
    await expect(firstTicker).toBeVisible();
    await firstTicker.click();
    const chart = section.locator(".recharts-wrapper").first();
    await expect(chart).toBeVisible({ timeout: 8000 });
    await expect(chart.locator(".recharts-line")).toBeVisible();
    expect(await chart.locator(".recharts-scatter").count()).toBeGreaterThanOrEqual(1);
    await expect(chart.getByText(/^Avg \$/)).toBeVisible();
    await firstTicker.click();
  });

  test("ticker with no prices shows fallback message", async ({ page }) => {
    const { section } = await visibleActivityTable(page);
    await disableGroupedActivity(section);
    await expandActivityOverflow(section);
    const spaxxRow = section.getByRole("row", { name: /SPAXX source: fidelity/ });
    await expect(spaxxRow).toBeVisible();
    await spaxxRow.click();
    await expect(page.getByText(/Money market fund/)).toBeVisible({ timeout: 5000 });
  });
  test("home page redirects to finance", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/finance/);
    await expect(page.locator("h1")).toContainText("Dashboard for Yuer");
  });

  // ── UI Polish ────────────────────────────────────────────────────────

  test("back to top button appears on scroll", async ({ page }) => {
    // Scroll down
    await page.evaluate(() => window.scrollTo(0, 1000));
    const btn = page.getByLabel("Back to top");
    await expect(btn).toBeVisible();
  });

  // ── Dark Mode ──────────────────────────────────────────────────────────

  test("dark mode toggle works", async ({ page }) => {
    const html = page.locator("html");
    // Initially light (no dark class)
    await expect(html).not.toHaveClass(/dark/);
    // Click the visible dark mode toggle (desktop sidebar)
    const toggle = page.getByLabel("Switch to dark mode").first();
    await toggle.click();
    // Should now have dark class
    await expect(html).toHaveClass(/dark/);
    // Click again to go back (label changes in dark mode)
    await page.getByLabel(/switch to (light|dark) mode/i).first().click();
    await expect(html).not.toHaveClass(/dark/);
  });

  test("savings labels stay correct after brush move", async ({ page }) => {
    const section = page.locator("#cashflow");
    await section.scrollIntoViewIfNeeded();
    const labelList = section.locator(".recharts-label-list");
    await expect(labelList).toBeVisible({ timeout: 5000 });

    const allTextBefore = await section.locator("svg text").allTextContents();
    const labelsBefore = allTextBefore.filter((t) => /^\d+%$/.test(t));
    expect(labelsBefore.length).toBeGreaterThan(0);

    const traveller = page.locator(".recharts-brush-traveller").first();
    await expect(traveller).toBeVisible();
    await traveller.focus();
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press("ArrowRight");
      await page.waitForTimeout(50);
    }
    await page.waitForLoadState("networkidle");

    // Any remaining labels must have valid percentages (not stale data)
    const allTextAfter = await section.locator("svg text").allTextContents();
    const labelsAfter = allTextAfter.filter((t) => /^\d+%$/.test(t));
    for (const label of labelsAfter) {
      const pct = parseInt(label);
      expect(pct).toBeGreaterThan(0);
      expect(pct).toBeLessThanOrEqual(100);
    }
    // Brush should have narrowed the visible range (fewer or equal labels)
    expect(labelsAfter.length).toBeLessThanOrEqual(labelsBefore.length);
  });
});
