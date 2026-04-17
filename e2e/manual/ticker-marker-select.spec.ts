import { test, expect } from "@playwright/test";

test.use({ baseURL: "http://localhost:3000" });

test("clicking a B marker highlights and scrolls to matching table rows", async ({ page }) => {
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
  await page.waitForTimeout(800);

  // Count the first green circles' bounding box
  const buyCircle = dialog.locator("svg circle[fill='#009E73']").first();
  await buyCircle.waitFor({ timeout: 3_000 });
  const box = await buyCircle.boundingBox();
  if (!box) throw new Error("no box");

  // Click the B marker
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await page.waitForTimeout(400);

  // Expect at least one highlighted cell (data-date matches cluster member)
  const highlighted = dialog.locator("td[data-date].bg-emerald-100, td[data-date].bg-emerald-900\\/30");
  await expect(highlighted.first()).toBeVisible({ timeout: 2_000 });
  const count = await highlighted.count();
  expect(count).toBeGreaterThan(0);

  // Screenshot for visual confirmation
  await page.screenshot({ path: "test-results/ticker-marker-select.png" });

  // Click again to deselect
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await page.waitForTimeout(300);
  const stillHighlighted = await highlighted.count();
  expect(stillHighlighted).toBe(0);
});
