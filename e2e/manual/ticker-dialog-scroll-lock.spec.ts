import { test, expect } from "@playwright/test";

test.use({ baseURL: "http://localhost:3000" });

test("wheel over dialog does not scroll page behind", async ({ page }) => {
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
  await page.waitForTimeout(500);

  const bodyOverflowWhileOpen = await page.evaluate(() => document.body.style.overflow);
  expect(bodyOverflowWhileOpen).toBe("hidden");

  const scrollYBefore = await page.evaluate(() => window.scrollY);
  // Wheel over the chart area
  const box = await dialog.locator("> div").first().boundingBox();
  if (!box) throw new Error("no box");
  await page.mouse.move(box.x + box.width / 2, box.y + 200);
  await page.mouse.wheel(0, 800);
  await page.waitForTimeout(200);
  const scrollYAfter = await page.evaluate(() => window.scrollY);

  expect(scrollYAfter).toBe(scrollYBefore);

  // Close dialog and verify overflow is restored
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  const bodyOverflowAfterClose = await page.evaluate(() => document.body.style.overflow);
  expect(bodyOverflowAfterClose).not.toBe("hidden");
});
