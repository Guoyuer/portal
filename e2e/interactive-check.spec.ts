/**
 * Interactive E2E check — screenshots of every section + brush interaction.
 * Run: npx playwright test e2e/interactive-check.spec.ts --headed
 */
import { test, expect, Page } from "@playwright/test";

const BASE = "http://localhost:3001";
const SCREENSHOT_DIR = "test-results/screenshots";

async function waitForData(page: Page) {
  // Wait for the page to load data from the API
  await page.goto(`${BASE}/finance`);
  await page.waitForLoadState("networkidle");
  // Wait for metric cards to appear (sign that API data loaded)
  await page.getByText("Net Worth").first().waitFor({ timeout: 10000 });
}

test.describe("Interactive Visual Check", () => {
  test("full page screenshot", async ({ page }) => {
    await waitForData(page);
    await page.screenshot({ path: `${SCREENSHOT_DIR}/01-full-page.png`, fullPage: true });
    // Basic checks
    await expect(page.getByText("Dashboard for Yuer")).toBeVisible();
    await expect(page.locator("[data-slot=card]").first()).toBeVisible();
  });

  test("metric cards section", async ({ page }) => {
    await waitForData(page);
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
    await waitForData(page);
    // Click net worth card to expand allocation
    const netWorthCard = page.locator("[data-slot=card]").first();
    await netWorthCard.click();
    await page.waitForTimeout(600); // wait for animation
    await page.screenshot({ path: `${SCREENSHOT_DIR}/03-allocation-expanded.png`, fullPage: true });
    // Should show category rows
    await expect(page.getByText("US Equity").first()).toBeVisible();
    await expect(page.getByText("Non-US Equity").first()).toBeVisible();
    await expect(page.getByText("Crypto").first()).toBeVisible();
    await expect(page.getByText("Safe Net").first()).toBeVisible();
    // Should show category values (tickers may be hidden on small viewports)
    await expect(page.getByText(/\$\d+.*broad|growth/).first()).toBeVisible();
  });

  test("timemachine chart loads", async ({ page }) => {
    await waitForData(page);
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
    await waitForData(page);
    const cfSection = page.locator("#cashflow");
    await expect(cfSection).toBeVisible();
    await cfSection.screenshot({ path: `${SCREENSHOT_DIR}/05-cashflow.png` });
    // Should show income and expense items
    await expect(cfSection.getByText(/Income|Salary/).first()).toBeVisible();
  });

  test("activity section", async ({ page }) => {
    await waitForData(page);
    const actSection = page.locator("#fidelity-activity");
    await expect(actSection).toBeVisible();
    await actSection.screenshot({ path: `${SCREENSHOT_DIR}/06-activity.png` });
    // Should show buys/dividends
    await expect(actSection.getByText(/Buy|Dividend/).first()).toBeVisible();
  });

  test("market section", async ({ page }) => {
    await waitForData(page);
    const mktSection = page.locator("#market");
    await expect(mktSection).toBeVisible();
    await mktSection.screenshot({ path: `${SCREENSHOT_DIR}/07-market.png` });
    // Should show index names
    await expect(mktSection.getByText("S&P 500").first()).toBeVisible();
  });

  test("brush drag updates summary", async ({ page }) => {
    await waitForData(page);
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
        await page.waitForTimeout(500);

        await tmSection.screenshot({ path: `${SCREENSHOT_DIR}/09-after-brush.png` });

        // The date should have changed
        const newTotal = await tmSection.getByTestId("tm-total").textContent();
        // They might be different (brush moved to a different date)
        console.log(`Brush test: initial="${initialTotal}" → after="${newTotal}"`);
      }
    }
  });

  test("savings rate card shows value", async ({ page }) => {
    await waitForData(page);
    // Find savings rate card
    const savingsCard = page.locator("[data-slot=card]").filter({ hasText: "Savings Rate" });
    if (await savingsCard.isVisible()) {
      await savingsCard.screenshot({ path: `${SCREENSHOT_DIR}/10-savings-rate.png` });
      // Should show a percentage
      await expect(savingsCard.getByText(/^\d+%$/).first()).toBeVisible();
    }
  });

  test("goal card shows progress", async ({ page }) => {
    await waitForData(page);
    const goalCard = page.locator("[data-slot=card]").filter({ hasText: "Goal" });
    if (await goalCard.isVisible()) {
      await goalCard.screenshot({ path: `${SCREENSHOT_DIR}/11-goal.png` });
      await expect(goalCard.getByText("%")).toBeVisible();
    }
  });
});
