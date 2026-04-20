/**
 * CI version of the ticker-dialog interaction specs (promoted from e2e/manual/).
 * Runs against the mock API fixture and the static build — see playwright.config.ts.
 */
import { test, expect, Page, Locator } from "@playwright/test";

// Helpers ────────────────────────────────────────────────────────────────

async function openTickerDialog(page: Page, symbol?: string): Promise<Locator> {
  await page.goto("/finance");
  await page.getByTestId("page-title").waitFor();
  // Turn off group view so `.first()` lands on a real ticker row (inline chart path)
  await page.locator("#investment-activity").getByRole("checkbox", { name: /Group equivalent tickers/i }).uncheck();

  const row = symbol
    ? page.locator("tr.cursor-pointer", { hasText: symbol }).first()
    : page.locator("tr.cursor-pointer").first();
  await row.waitFor();
  await row.scrollIntoViewIfNeeded();
  await row.click();

  const inline = page.locator(".cursor-zoom-in").first();
  await inline.waitFor();
  await inline.click();

  const dialog = page.locator("dialog[open]");
  await dialog.waitFor();
  // Wait for at least one chart element to make sure content rendered
  await dialog.locator("svg").first().waitFor();
  return dialog;
}

// ── Dialog open / close ──────────────────────────────────────────────────

test.describe("Ticker dialog — open/close", () => {
  test("Escape dismisses dialog", async ({ page }) => {
    await openTickerDialog(page);
    await page.keyboard.press("Escape");
    await expect(page.locator("dialog[open]")).toHaveCount(0);
  });

  test("close button dismisses dialog", async ({ page }) => {
    const dialog = await openTickerDialog(page);
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(page.locator("dialog[open]")).toHaveCount(0);
  });

  test("backdrop click dismisses dialog", async ({ page }) => {
    await openTickerDialog(page);
    await page.mouse.click(10, 10);
    await expect(page.locator("dialog[open]")).toHaveCount(0);
  });

  test("clicking inside dialog keeps it open", async ({ page }) => {
    const dialog = await openTickerDialog(page);
    await dialog.locator("span.font-mono").first().click();
    await expect(page.locator("dialog[open]")).toHaveCount(1);
  });
});

// ── Body scroll lock while dialog is open ────────────────────────────────

test.describe("Ticker dialog — scroll lock", () => {
  test("body overflow is locked while open and restored after close", async ({ page }) => {
    await openTickerDialog(page);
    await expect.poll(async () => await page.evaluate(() => document.body.style.overflow)).toBe("hidden");

    const scrollBefore = await page.evaluate(() => window.scrollY);
    const dialog = page.locator("dialog[open]");
    const box = await dialog.locator("> div").first().boundingBox();
    if (!box) throw new Error("no box");
    await page.mouse.move(box.x + box.width / 2, box.y + 200);
    await page.mouse.wheel(0, 800);
    const scrollAfter = await page.evaluate(() => window.scrollY);
    expect(scrollAfter).toBe(scrollBefore);

    await page.keyboard.press("Escape");
    await expect.poll(async () => await page.evaluate(() => document.body.style.overflow)).not.toBe("hidden");
  });
});

// ── Resize ───────────────────────────────────────────────────────────────

test.describe("Ticker dialog — resize", () => {
  test("drag bottom-right corner shrinks the dialog", async ({ page }) => {
    const dialog = await openTickerDialog(page);
    const inner = dialog.locator("> div").first();
    const before = await inner.boundingBox();
    if (!before) throw new Error("no box");

    const startX = before.x + before.width - 4;
    const startY = before.y + before.height - 4;
    await page.mouse.move(startX, startY);
    await page.mouse.down();
    await page.mouse.move(startX - 300, startY - 200, { steps: 20 });
    await page.mouse.up();

    const after = await inner.boundingBox();
    if (!after) throw new Error("no box after");
    expect(after.width).toBeLessThan(before.width);
    expect(after.height).toBeLessThan(before.height);
  });
});

// ── Transaction table: 2-column layout ───────────────────────────────────

test.describe("Ticker dialog — 2-col transaction table", () => {
  test("Date/Type/Qty/Price/Amount headers each appear exactly twice", async ({ page }) => {
    const dialog = await openTickerDialog(page);
    const headers = await dialog.locator("thead th").allTextContents();
    for (const label of ["Date", "Type", "Qty", "Price", "Amount"] as const) {
      expect(
        headers.filter((t) => t.trim() === label).length,
        `expected exactly 2 '${label}' headers`,
      ).toBe(2);
    }
  });
});

// ── Marker click → highlight rows, side-aware ────────────────────────────

test.describe("Ticker dialog — marker selection highlights matching-side cells", () => {
  test("clicking a buy circle highlights buy/reinvestment rows only", async ({ page }) => {
    const dialog = await openTickerDialog(page, "VOO");
    const buyCircle = dialog.locator("svg circle[fill='#009E73']").first();
    await buyCircle.waitFor();
    const box = await buyCircle.boundingBox();
    if (!box) throw new Error("no circle box");

    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);

    const highlighted = dialog.locator("td[data-date].bg-emerald-100, td[data-date].bg-emerald-900\\/30");
    await expect(highlighted.first()).toBeVisible();
    const sides = await highlighted.evaluateAll((els) =>
      (els as HTMLElement[]).map((e) => e.dataset.side ?? ""),
    );
    expect(sides.length).toBeGreaterThan(0);
    expect(sides.every((s) => s === "buy"), `sell cells leaked in: ${sides.join(",")}`).toBe(true);

    // Deselect → highlight cleared
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await expect(highlighted).toHaveCount(0);
  });
});

// ── Cluster tooltip on hover ─────────────────────────────────────────────

test.describe("Ticker dialog — cluster tooltip on marker hover", () => {
  test("hovering a buy circle shows the custom tooltip with Buy badge", async ({ page }) => {
    const dialog = await openTickerDialog(page, "VOO");
    const buyCircle = dialog.locator("svg circle[fill='#009E73']").first();
    await buyCircle.waitFor();
    const box = await buyCircle.boundingBox();
    if (!box) throw new Error("no circle box");
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);

    await expect.poll(async () => {
      return await page.evaluate(() => {
        const divs = Array.from(document.querySelectorAll<HTMLDivElement>("dialog[open] div"));
        const tt = divs.find((d) => d.style.position === "fixed" && /Buy|Sell/.test(d.textContent ?? ""));
        return tt?.textContent ?? "";
      });
    }).toMatch(/Buy/);
  });
});
