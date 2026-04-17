import { test, expect } from "@playwright/test";

test.use({ baseURL: "http://localhost:3000" });

test("drag resize handle shrinks dialog", async ({ page }) => {
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
  const inner = dialog.locator("> div").first();
  await page.waitForTimeout(500);

  const before = await inner.boundingBox();
  console.log("before drag:", before);
  if (!before) throw new Error("no box");

  // Drag the bottom-right resize corner from (right, bottom) up-left by 300,200
  const startX = before.x + before.width - 4;
  const startY = before.y + before.height - 4;
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(startX - 300, startY - 200, { steps: 20 });
  await page.mouse.up();
  await page.waitForTimeout(300);

  const after = await inner.boundingBox();
  console.log("after drag:", after);
  if (!after) throw new Error("no box after");

  expect(after.width).toBeLessThan(before.width);
  expect(after.height).toBeLessThan(before.height);

  await page.screenshot({ path: "test-results/ticker-resized.png" });
});
