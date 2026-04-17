/**
 * One-off screenshot + interaction check for the ticker chart dialog.
 * Run against the running dev server (localhost:3000 + worker 8787):
 *   npx playwright test --config=playwright.manual.config.ts ticker-dialog-check
 */
import { test, expect, Page } from "@playwright/test";

test.use({ baseURL: "http://localhost:3000" });

async function openDialog(page: Page) {
  await page.goto("/finance");
  await page.getByTestId("page-title").waitFor({ timeout: 15_000 });
  await page.waitForTimeout(1_500);

  const firstTickerRow = page.locator("tr.cursor-pointer").first();
  await firstTickerRow.waitFor({ timeout: 10_000 });
  await firstTickerRow.scrollIntoViewIfNeeded();
  await firstTickerRow.click();

  const inlineChart = page.locator(".cursor-zoom-in").first();
  await inlineChart.waitFor({ timeout: 10_000 });
  await page.waitForTimeout(1_000);
  await inlineChart.click();

  const dialog = page.locator("dialog[open]");
  await dialog.waitFor({ timeout: 5_000 });
  return dialog;
}

test("visual snapshot — inline + dialog", async ({ page }) => {
  await page.goto("/finance");
  await page.getByTestId("page-title").waitFor({ timeout: 15_000 });
  await page.waitForTimeout(1_500);

  const firstTickerRow = page.locator("tr.cursor-pointer").first();
  await firstTickerRow.waitFor({ timeout: 10_000 });
  await firstTickerRow.scrollIntoViewIfNeeded();
  await firstTickerRow.click();

  const inlineChart = page.locator(".cursor-zoom-in").first();
  await inlineChart.waitFor({ timeout: 10_000 });
  await page.waitForTimeout(1_000);
  await page.screenshot({ path: "test-results/ticker-01-inline.png" });

  await inlineChart.click();
  const dialog = page.locator("dialog[open]");
  await dialog.waitFor({ timeout: 5_000 });
  await page.waitForTimeout(1_000);
  await page.screenshot({ path: "test-results/ticker-02-dialog.png" });
});

test("close button dismisses dialog", async ({ page }) => {
  const dialog = await openDialog(page);
  await expect(dialog).toBeVisible();

  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(page.locator("dialog[open]")).toHaveCount(0, { timeout: 2_000 });
});

test("Escape dismisses dialog", async ({ page }) => {
  const dialog = await openDialog(page);
  await expect(dialog).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(page.locator("dialog[open]")).toHaveCount(0, { timeout: 2_000 });
});

test("backdrop click dismisses dialog", async ({ page }) => {
  const dialog = await openDialog(page);
  await expect(dialog).toBeVisible();

  // Click near top-left corner of viewport (backdrop area, outside the 900px-wide dialog card)
  await page.mouse.click(10, 10);
  await expect(page.locator("dialog[open]")).toHaveCount(0, { timeout: 2_000 });
});

test("clicking inside dialog does NOT close it or re-toggle row", async ({ page }) => {
  const dialog = await openDialog(page);
  await expect(dialog).toBeVisible();

  // Click on the symbol header (inside dialog) — should be a no-op
  await dialog.locator("span.font-mono").first().click();
  await page.waitForTimeout(300);
  await expect(page.locator("dialog[open]")).toHaveCount(1);

  // Also verify the underlying ticker row is still expanded (chart still in DOM)
  await expect(page.locator(".cursor-zoom-in").first()).toBeVisible();
});
