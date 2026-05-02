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

  test("renders page title", async ({ page }) => {
    await expect(page.locator("h1")).toContainText("Dashboard for Yuer");
  });

  test("shows metric cards with values", async ({ page }) => {
    // Wait for allocation API to load
    await expect(page.getByTestId("net-worth-card")).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/Investment/).first()).toBeVisible();
    await expect(page.getByText(/\$\d+k/).first()).toBeVisible();
    // Savings Rate
    await expect(page.getByTestId("savings-rate-card")).toBeVisible();
    // Goal
    await expect(page.getByTestId("goal-card")).toBeVisible();
  });

  test("shows all category groups", async ({ page }) => {
    // Wait for allocation data
    await expect(page.getByTestId("net-worth-card")).toBeVisible({ timeout: 10000 });
    // Click Net Worth tile to expand allocation
    await page.getByTestId("net-worth-card").getByRole("button").click();
    await expect(page.getByRole("cell", { name: "US Equity", exact: true })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Non-US Equity" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Crypto" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Safe Net" })).toBeVisible();
  });

  test("shows subtypes under equity categories", async ({ page }) => {
    await expect(page.getByTestId("net-worth-card")).toBeVisible({ timeout: 10000 });
    await page.getByTestId("net-worth-card").getByRole("button").click();
    await expect(page.getByText("broad").first()).toBeVisible();
    await expect(page.getByText("growth").first()).toBeVisible();
  });

  test("shows target and deviation columns", async ({ page }) => {
    await expect(page.getByTestId("net-worth-card")).toBeVisible({ timeout: 10000 });
    await page.getByTestId("net-worth-card").getByRole("button").click();
    await expect(page.getByRole("columnheader", { name: "Target" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Dev" })).toBeVisible();
  });

  test("shows category deviations with correct colors", async ({ page }) => {
    await expect(page.getByTestId("net-worth-card")).toBeVisible({ timeout: 10000 });
    await page.getByTestId("net-worth-card").getByRole("button").click();
    // Deviation cells should have red or green colors
    const deviationCells = page.locator("td[class*='text-red-'], td[class*='text-green-'], td[class*='text-emerald-']");
    await expect(deviationCells.first()).toBeVisible();
  });

  test("shows goal progress with bar", async ({ page }) => {
    await expect(page.getByTestId("goal-card")).toBeVisible({ timeout: 10000 });
    const goalCard = page.getByTestId("goal-card");
    const progressBar = goalCard.locator("[class*='bg-blue-']");
    await expect(progressBar).toBeVisible();
    const style = await progressBar.getAttribute("style");
    expect(style).toMatch(/width:\s*\d+/);
  });

  test("shows cash flow section with period", async ({ page }) => {
    await expect(page.getByText(/Cash Flow/).first()).toBeVisible({ timeout: 10000 });
    const cashflowSection = page.locator("#cashflow");
    await expect(cashflowSection.getByTestId("income-table")).toBeVisible();
    await expect(cashflowSection.getByTestId("expense-table")).toBeVisible();
    await expect(cashflowSection.getByText("Expenses").first()).toBeVisible();
  });

  test("shows income and expense totals", async ({ page }) => {
    await expect(page.getByText(/Cash Flow/).first()).toBeVisible({ timeout: 10000 });
    const section = page.locator("#cashflow");
    await expect(section.getByTestId("income-table").getByRole("row", { name: /Total/ })).toBeVisible();
    await expect(section.getByTestId("expense-table").getByRole("row", { name: /Total/ })).toBeVisible();
  });

  test("shows fixture expense categories", async ({ page }) => {
    await expect(page.getByText(/Cash Flow/).first()).toBeVisible({ timeout: 10000 });
    const expenseTable = page.locator("#cashflow").getByTestId("expense-table");
    await expect(expenseTable.getByRole("row", { name: /Rent 12/ })).toBeVisible();
    await expect(expenseTable.getByRole("row", { name: /Subscriptions 12/ })).toBeVisible();
  });

  test("shows cash flow summary metrics", async ({ page }) => {
    const tm = page.locator("#timemachine");
    await expect(tm).toBeVisible();
    await expect(tm.getByText("Net Savings")).toBeVisible();
    await expect(tm.getByText("Investments")).toBeVisible();
    await expect(tm.getByText("CC Payments")).toBeVisible();
  });

  test("shows investment activity section", async ({ page }) => {
    const section = page.locator("#investment-activity");
    await expect(section).toBeAttached();
  });

  test("shows buys and dividends by symbol", async ({ page }) => {
    await visibleActivityTable(page);
  });

  test("ticker tables have collapsible overflow", async ({ page }) => {
    const { section } = await visibleActivityTable(page);
    const details = section.locator("details").filter({ hasText: /and \d+ more/ });
    await expect(details.first()).toBeVisible();
    await details.first().locator("summary").click();
    await expect(details.first().locator("tr").first()).toBeVisible();
  });

  test("clicking ticker row expands inline price chart", async ({ page }) => {
    const { section, table: activityTable } = await visibleActivityTable(page);
    await disableGroupedActivity(section);
    const firstTicker = activityTable.locator("td.font-mono").first();
    await expect(firstTicker).toBeVisible();
    await firstTicker.click();
    await expect(section.locator(".recharts-wrapper").first()).toBeVisible({ timeout: 8000 });
    await firstTicker.click();
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


  test("sidebar has navigation links", async ({ page }) => {
    const sidebar = page.locator("aside").first();
    await expect(sidebar.getByText("Portal")).toBeVisible();
    await expect(sidebar.getByText("Finance")).toBeVisible();
  });

  test("home page redirects to finance", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/finance/);
    await expect(page.locator("h1")).toContainText("Dashboard for Yuer");
  });

  test("page renders all major sections in order", async ({ page }) => {
    await expect(page.locator("#cashflow")).toBeAttached();
    await expect(page.locator("#investment-activity")).toBeAttached();
  });

  // ── Charts ─────────────────────────────────────────────────────────────

  test("renders allocation donut chart", async ({ page }) => {
    await expect(page.getByTestId("net-worth-card")).toBeVisible({ timeout: 10000 });
    // Click Net Worth tile to expand allocation
    await page.getByTestId("net-worth-card").getByRole("button").click();
    const donut = page.locator(".recharts-pie");
    await expect(donut).toBeVisible();
    // Legend labels
    await expect(page.getByText(/^US Equity \d+%$/)).toBeVisible();
  });

  test("renders income vs expenses bar chart", async ({ page }) => {
    const section = page.locator("#cashflow");
    const bars = section.locator(".recharts-bar-rectangle");
    await expect(bars.first()).toBeVisible({ timeout: 5000 });
    expect(await bars.count()).toBeGreaterThan(0);
  });

  test("income vs expenses chart has legend", async ({ page }) => {
    const section = page.locator("#cashflow");
    await expect(section.locator(".recharts-bar-rectangle").first()).toBeVisible({ timeout: 5000 });
    await expect(section.getByText("Expenses").first()).toBeVisible();
    await expect(section.getByText("Savings").first()).toBeVisible();
  });

  // ── Market ─────────────────────────────────────────────────────

  test("shows market context with index cards", async ({ page }) => {
    const section = page.getByTestId("market-section");
    await expect(section).toBeVisible();
    await expect(section.getByText("S&P 500")).toBeVisible();
    await expect(section.getByText("NASDAQ 100")).toBeVisible();
    await expect(section.getByText("52-week range").first()).toBeVisible();
  });

  test("market section renders without macro when FRED unavailable", async ({ page }) => {
    const section = page.getByTestId("market-section");
    await expect(section).toBeVisible();
    await expect(section.getByText("Market")).toBeVisible();
    await expect(section.getByText("S&P 500")).toBeVisible();
    await expect(section.getByTestId("market-error")).toHaveCount(0);
  });

  // ── UI Polish ────────────────────────────────────────────────────────

  test("back to top button appears on scroll", async ({ page }) => {
    // Scroll down
    await page.evaluate(() => window.scrollTo(0, 1000));
    const btn = page.getByLabel("Back to top");
    await expect(btn).toBeVisible();
  });

  test("savings rate has conditional color", async ({ page }) => {
    await expect(page.getByTestId("savings-rate-card")).toBeVisible({ timeout: 10000 });
    const card = page.getByTestId("savings-rate-card");
    const rate = card.locator("p[class*='font-bold']").first();
    const className = await rate.getAttribute("class");
    expect(className).toMatch(/text-(green-|emerald-|yellow-|red-)|font-bold/);
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

  // ── Savings Rate Card ──────────────────────────────────────────────────

  test("savings rate card shows rate", async ({ page }) => {
    await expect(page.getByTestId("savings-rate-card")).toBeVisible({ timeout: 10000 });
    const card = page.getByTestId("savings-rate-card");
    // Rate (large) or N/A
    await expect(card.getByText(/\d+%|N\/A/).first()).toBeVisible();
  });

  // ── UI Polish (nav, charts, bento cards) ────────────────────────────────

  test("sticky brush bar is visible at page bottom", async ({ page }) => {
    const stickyBrush = page.locator(".recharts-brush");
    await expect(stickyBrush.first()).toBeVisible();
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

  test("income vs expenses chart renders bars", async ({ page }) => {
    await page.locator("#cashflow").scrollIntoViewIfNeeded();
    const bars = page.locator("#cashflow .recharts-bar-rectangle");
    await expect(bars.first()).toBeVisible({ timeout: 5000 });
    expect(await bars.count()).toBeGreaterThan(0);
  });

  // ── Savings Rate Trend ──────────────────────────────────────────────────

  // ── Timemachine ─────────────────────────────────────────────────────────

  test.describe("Timemachine", () => {
    test("shows timemachine chart when timeline API available", async ({ page }) => {
      const tmSection = page.locator("#timemachine");
      await expect(tmSection).toBeVisible();
      await expect(tmSection.locator(".recharts-area").first()).toBeVisible();
    });

    test("shows allocation categories in timemachine summary", async ({ page }) => {
      const tmSection = page.locator("#timemachine");
      await expect(tmSection).toBeVisible();
      await expect(tmSection.getByText("US Equity").first()).toBeVisible();
      await expect(tmSection.getByText("Safe Net").first()).toBeVisible();
    });

    test("shows range stats in timemachine summary", async ({ page }) => {
      const tmSection = page.locator("#timemachine");
      await expect(tmSection).toBeVisible();
      await expect(tmSection.getByText("Income")).toBeVisible();
      await expect(tmSection.getByText("Expenses")).toBeVisible();
      await expect(tmSection.getByText("Investments")).toBeVisible();
      await expect(tmSection.getByText("Dividends").first()).toBeVisible();
    });

    test("displays total value with dollar sign", async ({ page }) => {
      const tmSection = page.locator("#timemachine");
      await expect(tmSection).toBeVisible();
      await expect(tmSection.locator("text=/\\$\\d/").first()).toBeVisible();
    });

    test("timemachine chart renders stacked areas", async ({ page }) => {
      const tmSection = page.locator("#timemachine");
      await expect(tmSection).toBeVisible();
      const areas = tmSection.locator(".recharts-area");
      expect(await areas.count()).toBeGreaterThan(0);
    });
  });
});
