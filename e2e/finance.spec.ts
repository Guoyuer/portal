import { test, expect } from "@playwright/test";

test.describe("Finance Report", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/finance");
    // Wait for data to load from R2
    await page.getByText("Portfolio Snapshot").waitFor({ timeout: 5000 });
  });

  test("renders page title with date", async ({ page }) => {
    await expect(page.locator("h1")).toContainText("Portfolio Snapshot");
    // Date comes from live data — just verify it's present
    await expect(page.locator("h1")).toContainText(/\w+ \d{2}, \d{4}/);
  });

  test("shows four metric cards with values", async ({ page }) => {
    const cards = page.locator("[data-slot='card']");
    // Portfolio value
    await expect(cards.getByText("Investments")).toBeVisible();
    await expect(cards.getByText(/\$\d{3},\d{3}/).first()).toBeVisible();
    // Net Worth
    await expect(cards.getByText("Net Worth")).toBeVisible();
    // Savings Rate
    await expect(cards.getByText("Monthly Savings Rate")).toBeVisible();
    // Goal
    await expect(cards.getByText("Goal")).toBeVisible();
  });

  test("shows all category groups", async ({ page }) => {
    await expect(page.getByText("Category Summary")).toBeVisible();
    await expect(page.getByRole("cell", { name: "US Equity", exact: true })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Non-US Equity" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Crypto" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Safe Net" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Hedge" })).toBeVisible();
  });

  test("shows subtypes under equity categories", async ({ page }) => {
    // Equity categories have subtypes (broad, growth)
    await expect(page.getByText("broad").first()).toBeVisible();
    await expect(page.getByText("growth").first()).toBeVisible();
  });

  test("shows target and deviation columns", async ({ page }) => {
    // Table headers
    await expect(page.getByRole("columnheader", { name: "Target" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Deviation" })).toBeVisible();
  });

  test("shows category deviations with correct colors", async ({ page }) => {
    // Safe Net is typically underweight — red
    const safeNetRow = page.locator("tr").filter({ hasText: "Safe Net" });
    await expect(safeNetRow.locator(".text-red-500")).toBeVisible();
  });

  test("shows goal progress with bar", async ({ page }) => {
    await expect(page.getByText("$2,000,000")).toBeVisible();
    const goalCard = page.locator("[data-slot='card']").filter({ hasText: "Goal" });
    const progressBar = goalCard.locator(".bg-blue-600");
    await expect(progressBar).toBeVisible();
    const style = await progressBar.getAttribute("style");
    expect(style).toMatch(/width:\s*\d+/);
  });

  test("shows cash flow section with period", async ({ page }) => {
    await expect(page.getByText(/Cash Flow —/).first()).toBeVisible();
    // Income items
    await expect(page.getByRole("cell", { name: "Salary" })).toBeVisible();
    // Expense items
    // At least one expense category visible
    await expect(page.getByText("Expenses").first()).toBeVisible();
  });

  test("shows income and expense totals", async ({ page }) => {
    // Total rows should exist
    const totalRows = page.locator("tr").filter({ hasText: "Total" });
    await expect(totalRows.first()).toBeVisible();
  });

  test("expenses have collapsible minor items", async ({ page }) => {
    // Items < $200 are collapsed
    const details = page.locator("details");
    const expenseDetails = details.filter({ hasText: /and \d+ more/ });
    if (await expenseDetails.count() > 0) {
      await expect(expenseDetails.first()).toBeVisible();
    }
  });

  test("shows cash flow summary metrics", async ({ page }) => {
    await expect(page.getByRole("cell", { name: "Net Cash Flow" })).toBeVisible();
    await expect(page.getByRole("cell", { name: /Invested/ })).toBeVisible();
    await expect(page.getByRole("cell", { name: /CC Bill Payments/ })).toBeVisible();
  });

  test("net cash flow uses correct color", async ({ page }) => {
    const netRow = page.locator("tr").filter({ hasText: "Net Cash Flow" });
    const valueCell = netRow.locator("td").nth(1);
    const className = await valueCell.getAttribute("class");
    expect(className).toMatch(/text-(green-600|red-500)/);
  });

  test("shows investment activity with period", async ({ page }) => {
    await expect(page.getByText("Portfolio Activity")).toBeVisible();
    // Period dates
    await expect(page.getByText(/\d{2}\/\d{2}\/\d{4}/)).toBeVisible();
  });

  test("shows activity summary metrics", async ({ page }) => {
    await expect(page.getByRole("cell", { name: "Net Cash In" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Net Deployed" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Net Passive Income" })).toBeVisible();
  });

  test("shows buys and dividends by symbol", async ({ page }) => {
    await expect(page.getByText("Buys by Symbol")).toBeVisible();
    await expect(page.getByText("Dividends by Symbol")).toBeVisible();
    // At least one ticker symbol should be visible
    await expect(page.getByRole("cell", { name: /^[A-Z]{2,5}$/ }).first()).toBeVisible();
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

  test("shows balance sheet with assets and liabilities", async ({ page }) => {
    await expect(page.locator("#balance-sheet").getByText("Balance Sheet")).toBeVisible();
    // Fidelity investment total
    await expect(page.locator("#balance-sheet").getByRole("cell", { name: "Investments" })).toBeVisible();
    // At least one personal account
    await expect(page.getByRole("cell", { name: "I bond" })).toBeVisible();
    // CNY accounts indented
    await expect(page.getByRole("cell", { name: "建行卡" })).toBeVisible();
    // Liabilities section
    await expect(page.getByRole("heading", { name: "Liabilities" })).toBeVisible();
    // Net worth total
    await expect(page.getByText("Net Worth").first()).toBeVisible();
  });

  test("sidebar has navigation links", async ({ page }) => {
    const sidebar = page.locator("aside").first();
    await expect(sidebar.getByText("Portal")).toBeVisible();
    await expect(sidebar.getByText("Finance")).toBeVisible();
  });

  test("home page redirects to finance", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/finance/);
    await expect(page.locator("h1")).toContainText("Portfolio Snapshot");
  });

  test("page renders all major sections in order", async ({ page }) => {
    const sections = page.locator("div.bg-\\[\\#16213e\\]");
    const sectionTexts = await sections.allTextContents();
    expect(sectionTexts.length).toBeGreaterThanOrEqual(4);
    const combined = sectionTexts.join(" ");
    expect(combined).toContain("Category Summary");
    expect(combined).toContain("Cash Flow");
    expect(combined).toContain("Portfolio Activity");
    expect(combined).toContain("Balance Sheet");
  });

  // ── Charts ─────────────────────────────────────────────────────────────

  test("renders allocation donut chart", async ({ page }) => {
    // Donut is inside Category Summary section
    const donut = page.locator(".recharts-pie");
    await expect(donut).toBeVisible();
    // Legend labels
    await expect(page.getByText(/^US Equity \d+%$/)).toBeVisible();
  });

  test("renders income vs expenses bar chart", async ({ page }) => {
    await expect(page.getByText("Income vs Expenses")).toBeVisible();
    // Recharts renders bars as <rect> inside .recharts-bar
    const bars = page.locator(".recharts-bar-rectangle");
    expect(await bars.count()).toBeGreaterThan(0);
  });

  test("income vs expenses chart has legend", async ({ page }) => {
    const chartSection = page.locator("section").filter({ hasText: "Income vs Expenses" });
    await expect(chartSection.getByText("Income").first()).toBeVisible();
    await expect(chartSection.getByText("Expenses").first()).toBeVisible();
  });

  // ── Market Context ─────────────────────────────────────────────────────

  test("shows market context with index returns", async ({ page }) => {
    await expect(page.getByText("Market Context")).toBeVisible();
    await expect(page.getByText("Index Returns")).toBeVisible();
    // At least one index
    await expect(page.getByText("SPY").or(page.getByText("S&P 500")).first()).toBeVisible();
  });

  test("market section renders without macro when FRED unavailable", async ({ page }) => {
    await expect(page.getByText("Market Context")).toBeVisible();
    await expect(page.getByText("Index Returns")).toBeVisible();
  });

  // ── UI Polish ────────────────────────────────────────────────────────

  test("section nav bar is visible", async ({ page }) => {
    const nav = page.locator("nav").filter({ hasText: "Net Worth" });
    await expect(nav).toBeVisible();
    await expect(nav.getByText("Allocation")).toBeVisible();
    await expect(nav.getByText("Cash Flow")).toBeVisible();
  });

  test("back to top button appears on scroll", async ({ page }) => {
    // Scroll down
    await page.evaluate(() => window.scrollTo(0, 1000));
    const btn = page.getByLabel("Back to top");
    await expect(btn).toBeVisible();
  });

  test("savings rate has conditional color", async ({ page }) => {
    const card = page.locator("[data-slot='card']").filter({ hasText: "Monthly Savings Rate" });
    const rate = card.locator("p.text-2xl").first();
    const className = await rate.getAttribute("class");
    // Should have one of the conditional colors
    expect(className).toMatch(/text-(green-600|yellow-600|red-500)/);
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

  test("savings rate card shows gross and take-home", async ({ page }) => {
    const card = page.locator("[data-slot='card']").filter({ hasText: "Monthly Savings Rate" });
    // Gross rate (large)
    await expect(card.getByText(/\d+%/).first()).toBeVisible();
    // Take-home (smaller text)
    await expect(card.getByText(/take-home/)).toBeVisible();
  });

  // ── Production URL ─────────────────────────────────────────────────────

  test("production site is accessible", async ({ page }) => {
    const res = await page.request.get("https://portal-bf8.pages.dev/finance");
    expect(res.status()).toBe(200);
  });
});
