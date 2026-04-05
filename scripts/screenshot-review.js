// Usage: node scripts/screenshot-review.js [light|dark|both]
// Captures key user journeys for visual regression review.
// Output: screenshots/ directory

const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = "http://localhost:3000";
const OUT = path.join(__dirname, "..", "screenshots");

const JOURNEYS = [
  // Finance page
  { name: "fin-top", url: "/finance", scroll: 0, desc: "Finance — header + metric cards" },
  { name: "fin-networth", url: "/finance", scrollTo: "#net-worth", desc: "Finance — net worth chart" },
  { name: "fin-allocation", url: "/finance", scrollTo: "#allocation", desc: "Finance — category summary + donut" },
  { name: "fin-cashflow", url: "/finance", scrollTo: "#cashflow", desc: "Finance — cash flow tables + bar chart" },
  { name: "fin-activity", url: "/finance", scrollTo: "#portfolio-activity", desc: "Finance — portfolio activity" },
  { name: "fin-balance", url: "/finance", scrollTo: "#balance-sheet", desc: "Finance — balance sheet" },
  { name: "fin-holdings", url: "/finance", scrollTo: "#holdings", desc: "Finance — holdings detail" },
  { name: "fin-market", url: "/finance", scrollTo: "#market", desc: "Finance — market context" },
  // Econ page
  { name: "econ-top", url: "/econ", scroll: 0, desc: "Econ — header + macro cards + toggle" },
  { name: "econ-rates", url: "/econ", scroll: 380, desc: "Econ — interest rates charts" },
  { name: "econ-inflation", url: "/econ", scroll: 1100, desc: "Econ — inflation CPI chart" },
  { name: "econ-labor", url: "/econ", scroll: 1700, desc: "Econ — unemployment + VIX + oil" },
  // Sidebar
  { name: "sidebar-desktop", url: "/finance", scroll: 0, clip: { x: 0, y: 0, width: 240, height: 900 }, desc: "Sidebar — desktop" },
];

async function run() {
  const mode = process.argv[2] || "both";
  const modes = mode === "both" ? ["light", "dark"] : [mode];

  fs.mkdirSync(OUT, { recursive: true });

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  for (const m of modes) {
    let lastUrl = "";
    for (const j of JOURNEYS) {
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
      await page.waitForTimeout(800);

      // Scroll
      if (j.scrollTo) {
        await page.evaluate((id) => document.querySelector(id)?.scrollIntoView({ block: "start" }), j.scrollTo);
      } else if (j.scroll != null) {
        await page.evaluate((y) => window.scrollTo(0, y), j.scroll);
      }
      await page.waitForTimeout(600);

      const file = path.join(OUT, `${j.name}-${m}.png`);
      await page.screenshot({ path: file, ...(j.clip ? { clip: j.clip } : {}) });
      console.log(`✓ ${file}`);
    }
  }

  await browser.close();
  console.log(`\nDone — ${modes.length * JOURNEYS.length} screenshots in ${OUT}`);
}

run().catch((e) => { console.error(e); process.exit(1); });
