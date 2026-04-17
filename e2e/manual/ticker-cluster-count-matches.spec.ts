/**
 * Regression: a cluster marker's ×N badge must equal the number of matching-side
 * transactions in the table after click-selection.
 *
 * Previously `count` tracked distinct chart DAYS, so a cluster spanning days
 * with multiple same-date transactions (e.g. 4 days / 6 txns) displayed ×4 but
 * highlighted 6 rows when clicked.
 *
 * Also depends on Buy Scatter painting above Sell Scatter so a same-date sell
 * diamond does not steal the click intended for a buy cluster.
 */
import { test, expect, Page, Locator } from "@playwright/test";

test.use({ baseURL: "http://localhost:3000" });

async function openDialogFor(page: Page, symbol: string): Promise<Locator> {
  const row = page.locator("tr.cursor-pointer", { hasText: symbol }).first();
  await row.scrollIntoViewIfNeeded();
  await row.click();
  const inline = page.locator(".cursor-zoom-in").first();
  await inline.waitFor();
  await inline.click();
  const dialog = page.locator("dialog[open]");
  await dialog.waitFor();
  // Wait until at least one buy circle is painted
  await dialog.locator("svg circle[fill='#009E73']").first().waitFor();
  return dialog;
}

async function closeDialog(page: Page) {
  await page.keyboard.press("Escape");
  await expect(page.locator("dialog[open]")).toHaveCount(0);
}

async function clusterCenter(page: Page, index: number): Promise<{ cx: number; cy: number; n: number } | null> {
  return page.evaluate((i) => {
    const groups = Array.from(document.querySelectorAll<SVGGElement>("dialog[open] svg g[style*='cursor']"));
    const buys = groups.filter((g) => {
      const c = Array.from(g.children).find((el) => el.tagName === "circle") as SVGCircleElement | undefined;
      if (!c || c.getAttribute("fill") !== "#009E73") return false;
      return Array.from(g.children).some((el) => el.tagName === "text" && /^×\d+$/.test(el.textContent ?? ""));
    });
    const g = buys[i];
    if (!g) return null;
    const c = Array.from(g.children).find((el) => el.tagName === "circle") as SVGCircleElement;
    const t = Array.from(g.children).find((el) => el.tagName === "text" && /^×\d+$/.test(el.textContent ?? "")) as SVGTextElement;
    const r = c.getBoundingClientRect();
    return { cx: r.x + r.width / 2, cy: r.y + r.height / 2, n: parseInt(/^×(\d+)$/.exec(t.textContent!)![1], 10) };
  }, index);
}

async function validateClusters(page: Page, dialog: Locator, symbol: string) {
  const nBadges = await dialog.locator("svg g[style*='cursor']").evaluateAll((gs) =>
    (gs as SVGGElement[]).filter((g) => {
      const c = Array.from(g.children).find((el) => el.tagName === "circle") as SVGCircleElement | undefined;
      if (!c || c.getAttribute("fill") !== "#009E73") return false;
      return Array.from(g.children).some((el) => el.tagName === "text" && /^×\d+$/.test(el.textContent ?? ""));
    }).length,
  );

  let validated = 0;
  for (let i = 0; i < nBadges; i++) {
    const info = await clusterCenter(page, i);
    if (!info) break;

    await page.mouse.click(info.cx, info.cy);
    const highlighted = dialog.locator("td[data-date].bg-emerald-100, td[data-date].bg-emerald-900\\/30");
    await expect(
      highlighted,
      `${symbol} buy cluster #${i} at (${Math.round(info.cx)},${Math.round(info.cy)}) says ×${info.n}`,
    ).toHaveCount(info.n);

    // Deselect
    await page.mouse.click(info.cx, info.cy);
    await expect(highlighted).toHaveCount(0);
    validated += 1;
  }
  return validated;
}

test("buy cluster ×N badge == highlighted transactions (SGOV, FNJHX)", async ({ page }) => {
  await page.goto("/finance");
  await page.getByTestId("page-title").waitFor();

  // SGOV (earlier screenshots show a ×2 cluster)
  const sgovDialog = await openDialogFor(page, "SGOV");
  const sgovChecked = await validateClusters(page, sgovDialog, "SGOV");
  await closeDialog(page);

  // FNJHX (the original failing case with a multi-txn-per-day cluster, e.g. ×3)
  const fnjhxDialog = await openDialogFor(page, "FNJHX");
  const fnjhxChecked = await validateClusters(page, fnjhxDialog, "FNJHX");
  await closeDialog(page);

  expect(sgovChecked + fnjhxChecked).toBeGreaterThan(0);
});
