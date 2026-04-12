import { chromium } from "playwright";

const browser = await chromium.launch();

async function shot(name, dark) {
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    colorScheme: dark ? "dark" : "light",
  });
  const page = await ctx.newPage();
  await page.goto("http://localhost:3000/finance", { waitUntil: "networkidle" });
  // scroll to market section
  await page.locator("#market").scrollIntoViewIfNeeded();
  await page.waitForTimeout(500);
  const el = await page.locator("#market").boundingBox();
  if (el) {
    await page.screenshot({ path: name, clip: { x: 0, y: el.y - 30, width: 1280, height: el.height + 80 } });
  } else {
    await page.screenshot({ path: name, fullPage: false });
  }
  await ctx.close();
}

await shot("market-light.png", false);
await shot("market-dark.png", true);
await browser.close();
console.log("Done");
