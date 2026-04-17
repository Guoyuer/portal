import { test, expect } from "@playwright/test";

test.use({ baseURL: "http://localhost:3000" });

test("hovering anywhere on a B marker triggers Buy tooltip", async ({ page }) => {
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

  const buyCircle = dialog.locator("svg circle[fill='#009E73']").first();
  await buyCircle.waitFor({ timeout: 3_000 });
  const box = await buyCircle.boundingBox();
  if (!box) throw new Error("no box");

  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  const r = box.width / 2;

  // Sample points across the marker (left edge, center, right edge, slightly above/below)
  const samples = [
    { x: cx - r * 0.8, y: cy, label: "left edge" },
    { x: cx, y: cy, label: "center" },
    { x: cx + r * 0.8, y: cy, label: "right edge" },
    { x: cx, y: cy - r * 0.6, label: "top" },
    { x: cx, y: cy + r * 0.6, label: "bottom" },
  ];

  for (const s of samples) {
    await page.mouse.move(s.x, s.y);
    await page.waitForTimeout(150);
    const result = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll<HTMLDivElement>("dialog[open] div"));
      const customTooltip = divs.find((d) => d.style.position === "fixed" && /Buy|Sell/.test(d.textContent ?? ""));
      return {
        hasCustom: !!customTooltip,
        text: customTooltip?.textContent ?? "",
      };
    });
    console.log(`${s.label} (${Math.round(s.x)},${Math.round(s.y)}): hasCustom=${result.hasCustom}`);
    expect(result.hasCustom, `custom tooltip missing at ${s.label}`).toBe(true);
  }

  await page.screenshot({ path: "test-results/ticker-tooltip-coverage.png" });
});
