import { chromium } from "playwright";

const browser = await chromium.launch();

async function shot(name, dark) {
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    colorScheme: dark ? "dark" : "light",
  });
  const page = await ctx.newPage();
  await page.goto("http://localhost:3000/finance", { waitUntil: "networkidle" });
  const section = page.locator("#cashflow");
  await section.scrollIntoViewIfNeeded();
  await page.waitForTimeout(500);
  const el = await section.boundingBox();
  if (el) {
    await page.screenshot({
      path: name,
      clip: { x: 0, y: el.y, width: 1280, height: el.height + 20 },
    });
  }
  await ctx.close();
}

await shot("cashflow-bar-dark.png", true);
await shot("cashflow-bar-light.png", false);
await browser.close();
console.log("Done");
