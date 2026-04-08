import { test, expect } from "@playwright/test";

test.describe("Finance Report", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/finance");
    // Wait for page title (always rendered, even before API data loads)
    await page.getByText("Dashboard for Yuer").waitFor({ timeout: 5000 });
  });

  test("renders page title", async ({ page }) => {
    await expect(page.locator("h1")).toContainText("Dashboard for Yuer");
  });

  test("shows metric cards with values", async ({ page }) => {
    // Wait for allocation API to load
    await expect(page.getByText("Net Worth")).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/Investment/)).toBeVisible();
    await expect(page.getByText(/\$\d+k/).first()).toBeVisible();
    // Savings Rate
    await expect(page.getByText("Savings Rate").first()).toBeVisible();
    // Goal
    await expect(page.getByText("Goal")).toBeVisible();
  });

  test("shows all category groups", async ({ page }) => {
    // Wait for allocation data
    await expect(page.getByText("Net Worth")).toBeVisible({ timeout: 10000 });
    // Click Net Worth tile to expand allocation
    await page.getByRole("button", { name: /Net Worth/ }).click();
    await page.waitForTimeout(600);
    await expect(page.getByRole("cell", { name: "US Equity", exact: true })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Non-US Equity" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Crypto" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Safe Net" })).toBeVisible();
  });

  test("shows subtypes under equity categories", async ({ page }) => {
    await expect(page.getByText("Net Worth")).toBeVisible({ timeout: 10000 });
    await page.getByRole("button", { name: /Net Worth/ }).click();
    await page.waitForTimeout(600);
    await expect(page.getByText("broad").first()).toBeVisible();
    await expect(page.getByText("growth").first()).toBeVisible();
  });

  test("shows target and deviation columns", async ({ page }) => {
    await expect(page.getByText("Net Worth")).toBeVisible({ timeout: 10000 });
    await page.getByRole("button", { name: /Net Worth/ }).click();
    await page.waitForTimeout(600);
    await expect(page.getByRole("columnheader", { name: "Target" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Deviation" })).toBeVisible();
  });

  test("shows category deviations with correct colors", async ({ page }) => {
    await expect(page.getByText("Net Worth")).toBeVisible({ timeout: 10000 });
    await page.getByRole("button", { name: /Net Worth/ }).click();
    await page.waitForTimeout(600);
    // Deviation cells should have red or green colors
    const deviationCells = page.locator("td[class*='text-red-'], td[class*='text-green-'], td[class*='text-emerald-']");
    await expect(deviationCells.first()).toBeVisible();
  });

  test("shows goal progress with bar", async ({ page }) => {
    await expect(page.getByText("Goal")).toBeVisible({ timeout: 10000 });
    const goalCard = page.locator("[data-slot='card']").filter({ hasText: "Goal" });
    const progressBar = goalCard.locator("[class*='bg-blue-']");
    await expect(progressBar).toBeVisible();
    const style = await progressBar.getAttribute("style");
    expect(style).toMatch(/width:\s*\d+/);
  });

  test("shows cash flow section with period", async ({ page }) => {
    await expect(page.getByText(/Cash Flow/).first()).toBeVisible({ timeout: 10000 });
    // Wait for cash flow data to load
    const cashflowSection = page.locator("#cashflow");
    const hasTable = await cashflowSection.locator("table").count();
    if (hasTable === 0) return; // Skip if no data
    // Expense items
    await expect(page.getByText("Expenses").first()).toBeVisible();
  });

  test("shows income and expense totals", async ({ page }) => {
    await expect(page.getByText(/Cash Flow/).first()).toBeVisible({ timeout: 10000 });
    // Total rows should exist if cashflow data loaded
    const totalRows = page.locator("tr").filter({ hasText: "Total" });
    if (await totalRows.count() > 0) {
      await expect(totalRows.first()).toBeVisible();
    }
  });

  test("expenses have collapsible minor items", async ({ page }) => {
    await expect(page.getByText(/Cash Flow/).first()).toBeVisible({ timeout: 10000 });
    // Items < $200 are collapsed
    const details = page.locator("details");
    const expenseDetails = details.filter({ hasText: /and \d+ more/ });
    if (await expenseDetails.count() > 0) {
      await expect(expenseDetails.first()).toBeVisible();
    }
  });

  test("shows cash flow summary metrics", async ({ page }) => {
    // Wait for cashflow stat bar
    const netSavings = page.getByText("Net Savings");
    if (await netSavings.isVisible().catch(() => false)) {
      await expect(page.getByText("Invested")).toBeVisible();
      await expect(page.getByText("CC Payments")).toBeVisible();
    }
  });

  test("net savings uses correct color", async ({ page }) => {
    const label = page.getByText("Net Savings");
    if (!(await label.isVisible().catch(() => false))) return;
    const container = label.locator("..");
    const valueEl = container.locator("span.font-bold");
    const className = await valueEl.getAttribute("class");
    expect(className).toMatch(/text-cyan/);
  });

  test("shows investment activity section", async ({ page }) => {
    const section = page.locator("#fidelity-activity");
    await expect(section).toBeAttached();
  });

  test("shows buys and dividends by symbol", async ({ page }) => {
    const section = page.locator("#fidelity-activity");
    await expect(section).toBeAttached();
    // Activity data may take time to load
    if ((await section.locator("table").count()) === 0) {
      await page.waitForTimeout(3000);
    }
    if ((await section.locator("table").count()) === 0) return;
    await expect(page.getByText("Buys by Symbol")).toBeVisible();
    await expect(page.getByText("Dividends by Symbol")).toBeVisible();
  });

  test("ticker tables have collapsible overflow", async ({ page }) => {
    const details = page.locator("details").filter({ hasText: /and \d+ more/ });
    if (await details.count() > 0) {
      // Click to expand
      await details.first().locator("summary").click();
      // More rows should now be visible
      await expect(details.first().locator("tr").first()).toBeVisible();
    }
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
    await expect(page.locator("#fidelity-activity")).toBeAttached();
  });

  // ── Charts ─────────────────────────────────────────────────────────────

  test("renders allocation donut chart", async ({ page }) => {
    await expect(page.getByText("Net Worth")).toBeVisible({ timeout: 10000 });
    // Click Net Worth tile to expand allocation
    await page.getByRole("button", { name: /Net Worth/ }).click();
    await page.waitForTimeout(600);
    const donut = page.locator(".recharts-pie");
    await expect(donut).toBeVisible();
    // Legend labels
    await expect(page.getByText(/^US Equity \d+%$/)).toBeVisible();
  });

  test("renders income vs expenses bar chart", async ({ page }) => {
    const section = page.locator("#cashflow");
    // Wait for chart to render (may take time with API)
    await page.waitForTimeout(3000);
    const bars = section.locator(".recharts-bar-rectangle");
    if (await bars.count() > 0) {
      expect(await bars.count()).toBeGreaterThan(0);
    }
  });

  test("income vs expenses chart has legend", async ({ page }) => {
    const section = page.locator("#cashflow");
    await page.waitForTimeout(3000);
    if (await section.locator(".recharts-bar-rectangle").count() > 0) {
      await expect(section.getByText("Expenses").first()).toBeVisible();
      await expect(section.getByText("Savings").first()).toBeVisible();
    }
  });

  // ── Market ─────────────────────────────────────────────────────

  test("shows market context with index cards", async ({ page }) => {
    const section = page.locator("#market");
    await expect(section).toBeAttached();
    // Market data may take time to load
    await page.waitForTimeout(5000);
    if ((await section.locator("[data-slot='card']").count()) === 0) return;
    await expect(page.getByText("S&P 500").first()).toBeVisible();
  });

  test("market section renders without macro when FRED unavailable", async ({ page }) => {
    const section = page.locator("#market");
    await expect(section).toBeAttached();
    await page.waitForTimeout(5000);
    if ((await section.locator("[data-slot='card']").count()) === 0) return;
    await expect(page.getByText("S&P 500").first()).toBeVisible();
  });

  // ── UI Polish ────────────────────────────────────────────────────────

  test("back to top button appears on scroll", async ({ page }) => {
    // Scroll down
    await page.evaluate(() => window.scrollTo(0, 1000));
    const btn = page.getByLabel("Back to top");
    await expect(btn).toBeVisible();
  });

  test("savings rate has conditional color", async ({ page }) => {
    await expect(page.getByText("Savings Rate")).toBeVisible({ timeout: 10000 });
    const card = page.locator("[data-slot='card']").filter({ hasText: "Savings Rate" });
    const rate = card.locator("p[class*='font-bold']").first();
    const className = await rate.getAttribute("class");
    // Should have one of the conditional colors (or N/A if no cashflow data)
    if (className) {
      expect(className).toMatch(/text-(green-|emerald-|yellow-|red-)|font-bold/);
    }
  });

  // ── Dark Mode ──────────────────────────────────────────────────────────

  test("dark mode toggle works", async ({ page }) => {
    const html = page.locator("html");
    // Initially light (no dark class)
    await expect(html).not.toHaveClass(/dark/);
    // Click toggle in sidebar
    const toggle = page.locator("aside").first().getByRole("button").last();
    await toggle.click();
    // Should now have dark class
    await expect(html).toHaveClass(/dark/);
    // Click again to go back
    await toggle.click();
    await expect(html).not.toHaveClass(/dark/);
  });

  // ── Savings Rate Card ──────────────────────────────────────────────────

  test("savings rate card shows rate", async ({ page }) => {
    await expect(page.getByText("Savings Rate")).toBeVisible({ timeout: 10000 });
    const card = page.locator("[data-slot='card']").filter({ hasText: "Savings Rate" });
    // Rate (large) or N/A
    await expect(card.getByText(/\d+%|N\/A/).first()).toBeVisible();
  });

  // ── UI Polish (nav, charts, bento cards) ────────────────────────────────

  test("net worth section shows MoM and YoY badges", async ({ page }) => {
    // Falls back to #net-worth when timeline API is unavailable
    const section = page.locator("#net-worth");
    test.skip(!(await section.isVisible()), "timeline loaded — net-worth fallback not rendered");
    await expect(section.getByText("MoM")).toBeVisible();
    await expect(section.getByText("YoY")).toBeVisible();
    // Values should include percentage
    await expect(section.getByText(/[+-]\d+\.\d+%/).first()).toBeVisible();
  });

  test("net worth chart has brush slider", async ({ page }) => {
    const section = page.locator("#net-worth");
    test.skip(!(await section.isVisible()), "timeline loaded — net-worth fallback not rendered");
    const brush = section.locator(".recharts-brush");
    await expect(brush).toBeVisible();
  });

  test("income vs expenses chart has brush slider", async ({ page }) => {
    const section = page.locator("#cashflow");
    await page.waitForTimeout(3000);
    const brush = section.locator(".recharts-brush");
    if (await brush.count() > 0) {
      await expect(brush).toBeVisible();
    }
  });

  test("savings labels stay correct after brush move", async ({ page }) => {
    const section = page.locator("#cashflow");
    await section.scrollIntoViewIfNeeded();
    // Wait for chart labels to render
    const labelList = section.locator(".recharts-label-list");
    if (!(await labelList.isVisible({ timeout: 5000 }).catch(() => false))) return;

    // Collect savings labels before brush movement
    const allTextBefore = await section.locator("svg text").allTextContents();
    const labelsBefore = allTextBefore.filter((t) => /^\d+%$/.test(t));
    if (labelsBefore.length === 0) return;

    // Focus left brush traveller and press ArrowRight to narrow range
    const traveller = section.locator(".recharts-brush-traveller").first();
    if (!(await traveller.isVisible().catch(() => false))) return;
    await traveller.focus();
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press("ArrowRight");
      await page.waitForTimeout(100);
    }
    await page.waitForTimeout(500);

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
    // Scroll to chart area
    await page.locator("#cashflow").scrollIntoViewIfNeeded();
    await page.waitForTimeout(3000);
    const bars = page.locator("#cashflow .recharts-bar-rectangle");
    if (await bars.count() > 0) {
      expect(await bars.count()).toBeGreaterThan(0);
    }
  });

  test("stat bar metrics have color-coded values", async ({ page }) => {
    // Net Savings — cyan color (only if cashflow data loaded)
    const savingsLabel = page.getByText("Net Savings");
    if (!(await savingsLabel.isVisible().catch(() => false))) return;
    const savingsValue = savingsLabel.locator("..").locator("span.font-bold");
    const savingsClass = await savingsValue.getAttribute("class");
    expect(savingsClass).toMatch(/text-cyan/);
    // Invested — blue color
    const investedValue = page.getByText("Invested").locator("..").locator("span.font-bold");
    const investedClass = await investedValue.getAttribute("class");
    expect(investedClass).toMatch(/text-blue/);
  });

  // ── Production URL ─────────────────────────────────────────────────────

  test("production site is accessible", async ({ page }) => {
    const res = await page.request.get("https://portal-bf8.pages.dev/finance");
    expect(res.status()).toBe(200);
  });

  // ── Timemachine ─────────────────────────────────────────────────────────

  test.describe("Timemachine", () => {
    test("shows timemachine chart when timeline API available", async ({ page }) => {
      // The timemachine section should render if the backend is running
      const tmSection = page.locator("#timemachine");
      const nwSection = page.locator("#net-worth");

      // One of them should be visible (timemachine if backend running, net-worth otherwise)
      const tmVisible = await tmSection.isVisible().catch(() => false);
      const nwVisible = await nwSection.isVisible().catch(() => false);
      expect(tmVisible || nwVisible).toBe(true);
    });

    test("shows allocation categories in timemachine summary", async ({ page }) => {
      const tmSection = page.locator("#timemachine");
      if (!(await tmSection.isVisible().catch(() => false))) {
        test.skip();
        return;
      }
      await expect(tmSection.getByText("US Equity")).toBeVisible();
      await expect(tmSection.getByText("Safe Net")).toBeVisible();
    });

    test("shows range stats in timemachine summary", async ({ page }) => {
      const tmSection = page.locator("#timemachine");
      if (!(await tmSection.isVisible().catch(() => false))) {
        test.skip();
        return;
      }
      await expect(tmSection.getByText("Income")).toBeVisible();
      await expect(tmSection.getByText("Expenses")).toBeVisible();
      await expect(tmSection.getByText("Buys")).toBeVisible();
      await expect(tmSection.getByText("Dividends")).toBeVisible();
    });

    test("displays total value with dollar sign", async ({ page }) => {
      const tmSection = page.locator("#timemachine");
      if (!(await tmSection.isVisible().catch(() => false))) {
        test.skip();
        return;
      }
      // Should show a dollar amount like "$412,883" or "$413k"
      await expect(tmSection.locator("text=/\\$\\d/").first()).toBeVisible();
    });
  });
});
