/**
 * Verifies:
 * - Transaction table renders 2 transactions per visual row (doubled headers)
 * - Side-aware table-cell highlighting (sell selection must not highlight buy
 *   transactions, and vice versa).
 */
import { test, expect } from "@playwright/test";

test.use({ baseURL: "http://localhost:3000" });

test("table is 2-col layout and side-aware highlighting works", async ({ page }) => {
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
  await page.waitForTimeout(1_000);

  // ── Table has doubled headers (10 th's + a spacer) ──
  const headerCells = await dialog.locator("thead th").allTextContents();
  expect(headerCells.filter((t) => t.trim() === "Date").length).toBe(2);
  expect(headerCells.filter((t) => t.trim() === "Type").length).toBe(2);
  expect(headerCells.filter((t) => t.trim() === "Qty").length).toBe(2);
  expect(headerCells.filter((t) => t.trim() === "Price").length).toBe(2);
  expect(headerCells.filter((t) => t.trim() === "Amount").length).toBe(2);

  // ── Click a buy marker: only buy/reinvestment cells should be highlighted ──
  const buyCircle = dialog.locator("svg circle[fill='#009E73']").first();
  await buyCircle.waitFor({ timeout: 3_000 });
  const buyBox = await buyCircle.boundingBox();
  if (!buyBox) throw new Error("no buy box");
  await page.mouse.click(buyBox.x + buyBox.width / 2, buyBox.y + buyBox.height / 2);
  await page.waitForTimeout(400);
  const buyHighlighted = dialog.locator("td[data-date].bg-emerald-100, td[data-date].bg-emerald-900\\/30");
  await expect(buyHighlighted.first()).toBeVisible({ timeout: 2_000 });

  const buySides = await buyHighlighted.evaluateAll((els) =>
    (els as HTMLElement[]).map((e) => e.dataset.side ?? ""),
  );
  expect(buySides.length).toBeGreaterThan(0);
  expect(buySides.every((s) => s === "buy"), `sell cells leaked into buy selection: ${buySides.join(",")}`).toBe(true);

  await page.screenshot({ path: "test-results/ticker-table-2col-buy.png" });

  // Deselect
  await page.mouse.click(buyBox.x + buyBox.width / 2, buyBox.y + buyBox.height / 2);
  await page.waitForTimeout(300);

  // ── If sells exist: click a sell marker and verify no buy cells highlight ──
  const sellCircleCount = await dialog.locator("svg path[fill='#E69F00'], svg polygon[fill='#E69F00']").count();
  if (sellCircleCount > 0) {
    const sellMarker = dialog.locator("svg path[fill='#E69F00'], svg polygon[fill='#E69F00']").first();
    const sellBox = await sellMarker.boundingBox();
    if (!sellBox) throw new Error("no sell box");
    await page.mouse.click(sellBox.x + sellBox.width / 2, sellBox.y + sellBox.height / 2);
    await page.waitForTimeout(400);

    const sellHighlighted = dialog.locator("td[data-date].bg-amber-100, td[data-date].bg-amber-900\\/30");
    await expect(sellHighlighted.first()).toBeVisible({ timeout: 2_000 });
    const sellSides = await sellHighlighted.evaluateAll((els) =>
      (els as HTMLElement[]).map((e) => e.dataset.side ?? ""),
    );
    expect(sellSides.length).toBeGreaterThan(0);
    expect(sellSides.every((s) => s === "sell"), `buy cells leaked into sell selection: ${sellSides.join(",")}`).toBe(true);

    await page.screenshot({ path: "test-results/ticker-table-2col-sell.png" });
  } else {
    console.log("No sell markers present on first ticker — skipping sell-side check");
  }
});
