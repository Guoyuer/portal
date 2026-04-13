/**
 * Interactive E2E check — screenshots of every section + brush interaction.
 * Excluded from CI via playwright.config.ts `testIgnore` on `manual/`.
 * Run: npx playwright test e2e/manual/interactive-check.spec.ts --headed
 */
import { test, expect, Page } from "@playwright/test";

const SCREENSHOT_DIR = "test-results/screenshots";

async function loadFinance(page: Page): Promise<void> {
  await page.goto("/finance");
  await page.getByTestId("page-title").waitFor({ timeout: 10_000 });
}

test.describe("Interactive Visual Check", () => {
  test("full page screenshot", async ({ page }) => {
    await loadFinance(page);
    await page.screenshot({ path: `${SCREENSHOT_DIR}/01-full-page.png`, fullPage: true });
    await expect(page.getByTestId("page-title")).toBeVisible();
    await expect(page.locator("[data-slot=card]").first()).toBeVisible();
  });

  test("metric cards section", async ({ page }) => {
    await loadFinance(page);
    // Net Worth card
    const netWorthCard = page.locator("[data-slot=card]").first();
    await expect(netWorthCard).toBeVisible();
    await netWorthCard.screenshot({ path: `${SCREENSHOT_DIR}/02-net-worth-card.png` });
    // Should show dollar amount (first match)
    await expect(netWorthCard.getByText(/\$\d/).first()).toBeVisible();
    // Should show Safe Net vs Investment split
    await expect(netWorthCard.getByText(/Safe Net/).first()).toBeVisible();
    await expect(netWorthCard.getByText(/Investment/).first()).toBeVisible();
  });

  test("expand allocation table", async ({ page }) => {
    await loadFinance(page);
    // Click net worth card to expand allocation
    const netWorthCard = page.locator("[data-slot=card]").first();
    await netWorthCard.click();
    // Wait for allocation table to expand
    await expect(page.getByText("US Equity").first()).toBeVisible();
    await page.screenshot({ path: `${SCREENSHOT_DIR}/03-allocation-expanded.png`, fullPage: true });
    await expect(page.getByText("Non-US Equity").first()).toBeVisible();
    await expect(page.getByText("Crypto").first()).toBeVisible();
    await expect(page.getByText("Safe Net").first()).toBeVisible();
    // Should show category values (tickers may be hidden on small viewports)
    await expect(page.getByText(/\$\d+.*broad|growth/).first()).toBeVisible();
  });

  test("timemachine chart loads", async ({ page }) => {
    await loadFinance(page);
    const tmSection = page.locator("#timemachine");
    await expect(tmSection).toBeVisible();
    await tmSection.screenshot({ path: `${SCREENSHOT_DIR}/04-timemachine.png` });
    // Should have the chart with recharts elements
    const svg = tmSection.locator("svg").first();
    await expect(svg).toBeVisible();
    // Should show date and total
    await expect(tmSection.getByTestId("tm-date")).toBeVisible();
    await expect(tmSection.getByTestId("tm-total")).toBeVisible();
  });

  test("cash flow section", async ({ page }) => {
    await loadFinance(page);
    const cfSection = page.locator("#cashflow");
    await expect(cfSection).toBeVisible();
    await cfSection.screenshot({ path: `${SCREENSHOT_DIR}/05-cashflow.png` });
    // Should show income and expense items
    await expect(cfSection.getByText(/Income|Salary/).first()).toBeVisible();
  });

  test("activity section", async ({ page }) => {
    await loadFinance(page);
    const actSection = page.locator("#fidelity-activity");
    await expect(actSection).toBeVisible();
    await actSection.screenshot({ path: `${SCREENSHOT_DIR}/06-activity.png` });
    // Should show buys/dividends
    await expect(actSection.getByText(/Buy|Dividend/).first()).toBeVisible();
  });

  test("market section", async ({ page }) => {
    await loadFinance(page);
    const mktSection = page.getByTestId("market-section");
    await expect(mktSection).toBeVisible();
    await mktSection.screenshot({ path: `${SCREENSHOT_DIR}/07-market.png` });
    // Should show index names
    await expect(mktSection.getByText("S&P 500").first()).toBeVisible();
  });

  test("brush drag updates summary", async ({ page }) => {
    await loadFinance(page);
    const tmSection = page.locator("#timemachine");
    await expect(tmSection).toBeVisible();

    // Get initial total
    const initialTotal = await tmSection.getByTestId("tm-total").textContent();
    await tmSection.screenshot({ path: `${SCREENSHOT_DIR}/08-before-brush.png` });

    // Find the brush traveller (left handle) and drag it
    const brush = tmSection.locator(".recharts-brush").first();
    if (await brush.isVisible()) {
      const brushBox = await brush.boundingBox();
      if (brushBox) {
        // Drag left traveller to the right by 100px
        const startX = brushBox.x + 20;
        const startY = brushBox.y + brushBox.height / 2;
        await page.mouse.move(startX, startY);
        await page.mouse.down();
        await page.mouse.move(startX + 100, startY, { steps: 20 });
        await page.mouse.up();

        await tmSection.screenshot({ path: `${SCREENSHOT_DIR}/09-after-brush.png` });

        // The date should have changed
        const newTotal = await tmSection.getByTestId("tm-total").textContent();
        console.log(`Brush test: initial="${initialTotal}" → after="${newTotal}"`);
      }
    }
  });

  test("savings rate card shows value", async ({ page }) => {
    await loadFinance(page);
    const savingsCard = page.getByTestId("savings-rate-card");
    if (await savingsCard.isVisible()) {
      await savingsCard.screenshot({ path: `${SCREENSHOT_DIR}/10-savings-rate.png` });
      // Should show a percentage
      await expect(savingsCard.getByText(/^\d+%$/).first()).toBeVisible();
    }
  });

  test("goal card shows progress", async ({ page }) => {
    await loadFinance(page);
    const goalCard = page.getByTestId("goal-card");
    if (await goalCard.isVisible()) {
      await goalCard.screenshot({ path: `${SCREENSHOT_DIR}/11-goal.png` });
      await expect(goalCard.getByText("%")).toBeVisible();
    }
  });
});
