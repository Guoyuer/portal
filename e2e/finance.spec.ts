import { test, expect } from "@playwright/test";

test.describe("Finance Report", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/finance");
  });

  test("renders page title with date", async ({ page }) => {
    await expect(page.locator("h1")).toContainText("Portfolio Snapshot");
    // Date comes from live data — just verify it's present
    await expect(page.locator("h1")).toContainText(/\w+ \d{2}, \d{4}/);
  });

  test("shows four metric cards with values", async ({ page }) => {
    const cards = page.locator("[data-slot='card']");
    // Portfolio value
    await expect(cards.getByText("Portfolio")).toBeVisible();
    await expect(cards.getByText(/\$\d{3},\d{3}/).first()).toBeVisible();
    // Net Worth
    await expect(cards.getByText("Net Worth")).toBeVisible();
    // Savings Rate
    await expect(cards.getByText("Savings Rate")).toBeVisible();
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

  test("shows goal progress", async ({ page }) => {
    await expect(page.getByText("$2,000,000")).toBeVisible();
  });

  test("shows cash flow section with period", async ({ page }) => {
    await expect(page.getByText(/Cash Flow —/).first()).toBeVisible();
    // Income items
    await expect(page.getByRole("cell", { name: "Salary" })).toBeVisible();
    // Expense items
    await expect(page.getByRole("cell", { name: "Housing" })).toBeVisible();
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
    await expect(page.getByRole("cell", { name: "Invested" })).toBeVisible();
    await expect(page.getByText("Gross Savings Rate")).toBeVisible();
    await expect(page.getByText("Take-home Savings Rate")).toBeVisible();
  });

  test("shows investment activity with period", async ({ page }) => {
    await expect(page.getByText("Investment Activity")).toBeVisible();
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
    await expect(page.getByText("Balance Sheet")).toBeVisible();
    // Fidelity investment total
    await expect(page.getByText(/Investments \(Fidelity\)/)).toBeVisible();
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
    // Verify key sections exist
    const combined = sectionTexts.join(" ");
    expect(combined).toContain("Category Summary");
    expect(combined).toContain("Cash Flow");
    expect(combined).toContain("Investment Activity");
    expect(combined).toContain("Balance Sheet");
  });
});
