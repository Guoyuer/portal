// Usage: node scripts/screenshot-review.js [light|dark|both]
// Captures key user journeys for visual regression review.
// Output: screenshots/desktop/ and screenshots/mobile/

const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = "http://localhost:3000";
const SCREENSHOTS = path.join(__dirname, "..", "screenshots");

const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 },
];

const JOURNEYS = [
  // Finance page
  { name: "fin-top", url: "/finance", scroll: 0 },
  { name: "fin-networth", url: "/finance", scrollTo: "#net-worth" },
  { name: "fin-allocation", url: "/finance", scrollTo: "#allocation" },
  { name: "fin-cashflow", url: "/finance", scrollTo: "#cashflow" },
  { name: "fin-activity", url: "/finance", scrollTo: "#fidelity-activity" },
  { name: "fin-holdings", url: "/finance", scrollTo: "#holdings" },
  { name: "fin-market", url: "/finance", scrollTo: "#market" },
  // Econ page
  { name: "econ-top", url: "/econ", scroll: 0 },
  { name: "econ-rates", url: "/econ", scroll: 380 },
  { name: "econ-inflation", url: "/econ", scroll: 1100 },
  { name: "econ-labor", url: "/econ", scroll: 1700 },
];

// Desktop-only: sidebar crop
const DESKTOP_EXTRAS = [
  { name: "sidebar", url: "/finance", scroll: 0, clip: { x: 0, y: 0, width: 240, height: 900 } },
];

// Mobile-only: open drawer
const MOBILE_EXTRAS = [
  { name: "sidebar-open", url: "/finance", scroll: 0, openDrawer: true },
];

async function captureViewport(browser, vp, modes) {
  const outDir = path.join(SCREENSHOTS, vp.name);
  fs.mkdirSync(outDir, { recursive: true });

  const page = await browser.newPage({ viewport: { width: vp.width, height: vp.height } });

  const allJourneys = [
    ...JOURNEYS,
    ...(vp.name === "desktop" ? DESKTOP_EXTRAS : MOBILE_EXTRAS),
  ];

  for (const m of modes) {
    let lastUrl = "";
    for (const j of allJourneys) {
      if (j.url !== lastUrl) {
        await page.goto(BASE + j.url, { waitUntil: "networkidle", timeout: 20000 });
        lastUrl = j.url;
      }

      // Set theme
      if (m === "dark") {
        await page.evaluate(() => { document.documentElement.classList.add("dark"); localStorage.setItem("theme", "dark"); });
      } else {
        await page.evaluate(() => { document.documentElement.classList.remove("dark"); localStorage.setItem("theme", "light"); });
      }
      await page.waitForTimeout(600);

      // Open mobile drawer if needed
      if (j.openDrawer) {
        await page.locator("button[aria-label='Toggle navigation']").click();
        await page.waitForTimeout(400);
      }

      // Scroll
      if (j.scrollTo) {
        await page.evaluate((id) => document.querySelector(id)?.scrollIntoView({ block: "start" }), j.scrollTo);
      } else if (j.scroll != null) {
        await page.evaluate((y) => window.scrollTo(0, y), j.scroll);
      }
      await page.waitForTimeout(500);

      const file = path.join(outDir, `${j.name}-${m}.png`);
      await page.screenshot({ path: file, ...(j.clip ? { clip: j.clip } : {}) });
      console.log(`  ✓ ${vp.name}/${j.name}-${m}.png`);

      // Close drawer after screenshot
      if (j.openDrawer) {
        await page.locator("button[aria-label='Toggle navigation']").click();
        await page.waitForTimeout(300);
      }
    }
  }

  await page.close();
}

async function run() {
  const mode = process.argv[2] || "both";
  const modes = mode === "both" ? ["light", "dark"] : [mode];

  const browser = await chromium.launch();

  for (const vp of VIEWPORTS) {
    console.log(`\n📱 ${vp.name} (${vp.width}x${vp.height})`);
    await captureViewport(browser, vp, modes);
  }

  await browser.close();

  const total = VIEWPORTS.reduce((n, vp) => {
    const extras = vp.name === "desktop" ? DESKTOP_EXTRAS.length : MOBILE_EXTRAS.length;
    return n + (JOURNEYS.length + extras) * modes.length;
  }, 0);
  console.log(`\nDone — ${total} screenshots in ${SCREENSHOTS}`);
}

run().catch((e) => { console.error(e); process.exit(1); });
