/**
 * Automated performance test — measures frame times during brush drag.
 * Uses rAF loop to capture per-frame durations + Long Task observer.
 * Requires local dev server + backend — skipped in CI.
 */
import { test as base, expect, Page } from "@playwright/test";

const test = base.extend({})
test.skip(() => !!process.env.CI, "Requires local dev server + backend");

const DEV_URL = "http://localhost:3000";
const PROD_URL = "http://localhost:3100";

async function measureBrushDrag(page: Page, label: string) {
  await page.getByText("Net Worth").first().waitFor({ timeout: 10000 });

  // Inject frame timing + long task observer
  await page.evaluate(() => {
    (window as any).__perfData = { frames: [] as number[], longTasks: [] as number[] };
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        (window as any).__perfData.longTasks.push(Math.round(entry.duration));
      }
    }).observe({ type: "longtask", buffered: false });
    let last = performance.now();
    function measure() {
      const now = performance.now();
      (window as any).__perfData.frames.push(Math.round(now - last));
      last = now;
      if ((window as any).__perfMeasuring) requestAnimationFrame(measure);
    }
    (window as any).__perfMeasuring = true;
    requestAnimationFrame(measure);
  });

  const brush = page.locator("#timemachine .recharts-brush").first();
  await expect(brush).toBeVisible();
  const box = await brush.boundingBox();
  if (!box) throw new Error("Brush not found");

  await page.evaluate(() => {
    (window as any).__perfData.frames = [];
    (window as any).__perfData.longTasks = [];
  });

  const startX = box.x + 20;
  const y = box.y + box.height / 2;
  await page.mouse.move(startX, y);
  await page.mouse.down();
  await page.mouse.move(startX + 200, y, { steps: 30 });
  await page.mouse.up();
  await page.waitForTimeout(300);

  await page.evaluate(() => { (window as any).__perfMeasuring = false; });

  const perf = await page.evaluate(() => (window as any).__perfData as {
    frames: number[];
    longTasks: number[];
  });

  const frames = perf.frames.filter((f) => f > 0);
  const slow = frames.filter((f) => f > 50);
  const sorted = [...frames].sort((a, b) => a - b);
  const p50 = sorted[Math.floor(sorted.length * 0.5)] ?? 0;
  const p95 = sorted[Math.floor(sorted.length * 0.95)] ?? 0;
  const max = Math.max(...frames, 0);

  console.log(`\n── ${label} ──`);
  console.log(`Frames: ${frames.length} | p50: ${p50}ms | p95: ${p95}ms | max: ${max}ms`);
  console.log(`Slow (>50ms): ${slow.length}${slow.length ? ` [${slow.join(", ")}]` : ""}`);
  console.log(`Long tasks: ${perf.longTasks.length}${perf.longTasks.length ? ` [${perf.longTasks.join(", ")}]` : ""}`);

  return { frames, p50, p95, max, slow: slow.length, longTasks: perf.longTasks.length };
}

test("brush perf: dev server", async ({ page }) => {
  await page.goto(`${DEV_URL}/finance`);
  const r = await measureBrushDrag(page, "DEV");
  expect(r.p95, `dev p95 ${r.p95}ms`).toBeLessThan(100);
});

test("brush perf: production build", async ({ page }) => {
  await page.goto(`${PROD_URL}/finance`);
  const r = await measureBrushDrag(page, "PROD");
  expect(r.p95, `prod p95 ${r.p95}ms`).toBeLessThan(50);
});
