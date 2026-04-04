import { test, expect } from "@playwright/test";

test.describe("Finance Report", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/finance");
  });

  test("renders page title", async ({ page }) => {
    await expect(page.locator("h1")).toContainText("Portfolio Snapshot");
    await expect(page.locator("h1")).toContainText("April 02, 2026");
  });

  test("shows metric cards", async ({ page }) => {
    // Use card-specific locators to avoid matching table cells
    const cards = page.locator("[data-slot='card']");
    await expect(cards.getByText("$410,921.73")).toBeVisible();
    await expect(cards.getByText("$414,754.43")).toBeVisible();
    await expect(cards.getByText("60%")).toBeVisible();
    await expect(cards.getByText("21%")).toBeVisible();
  });

  test("shows category summary", async ({ page }) => {
    await expect(page.getByText("Category Summary")).toBeVisible();
    await expect(page.getByRole("cell", { name: "US Equity", exact: true })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Non-US Equity" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Crypto" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Safe Net" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Hedge" })).toBeVisible();
    // Goal progress
    await expect(page.getByText("20.55%")).toBeVisible();
    await expect(page.getByText("$2,000,000")).toBeVisible();
  });

  test("shows category deviations with correct colors", async ({ page }) => {
    // Positive deviation — green
    const usEquityRow = page.locator("tr").filter({ hasText: /^US Equity/ });
    await expect(usEquityRow.locator(".text-green-600")).toBeVisible();
    // Negative deviation — red
    const safeNetRow = page.locator("tr").filter({ hasText: "Safe Net" });
    await expect(safeNetRow.locator(".text-red-500")).toBeVisible();
  });

  test("shows cash flow section", async ({ page }) => {
    await expect(page.getByText("Cash Flow — March 2026")).toBeVisible();
    // Income items
    await expect(page.getByRole("cell", { name: "Salary" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "$10,329.50" })).toBeVisible();
    // Expense items
    await expect(page.getByRole("cell", { name: "Housing" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "$2,155.02" })).toBeVisible();
    // Totals
    await expect(page.getByRole("cell", { name: "$13,802.68" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "$5,568.65" })).toBeVisible();
  });

  test("shows cash flow summary", async ({ page }) => {
    await expect(page.getByRole("cell", { name: "$8,234.03" })).toBeVisible();
    await expect(page.getByText("59.7%").first()).toBeVisible();
    await expect(page.getByText("47.1%").first()).toBeVisible();
  });

  test("shows investment activity", async ({ page }) => {
    await expect(page.getByText("Investment Activity")).toBeVisible();
    await expect(page.getByText("03/02/2026")).toBeVisible();
    // Buys by ticker
    await expect(page.getByRole("cell", { name: "FNJHX" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "GLDM" })).toBeVisible();
    // Dividends
    await expect(page.getByRole("cell", { name: "SGOV" })).toBeVisible();
  });

  test("shows balance sheet", async ({ page }) => {
    await expect(page.getByText("Balance Sheet")).toBeVisible();
    await expect(page.getByRole("cell", { name: "Fidelity (all accounts)" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Chase Debit" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "I bond" })).toBeVisible();
    // CNY accounts
    await expect(page.getByRole("cell", { name: "建行卡" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "微信零钱通" })).toBeVisible();
    // Liabilities
    await expect(page.getByRole("cell", { name: "Amex Gold" })).toBeVisible();
    // Net worth
    await expect(page.getByText("Net Worth").first()).toBeVisible();
  });

  test("sidebar has navigation", async ({ page }) => {
    const sidebar = page.locator("aside").first();
    await expect(sidebar.getByText("Portal")).toBeVisible();
    await expect(sidebar.getByText("Finance")).toBeVisible();
  });

  test("home page redirects to finance", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/finance/);
    await expect(page.locator("h1")).toContainText("Portfolio Snapshot");
  });
});
