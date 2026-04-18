import { defineConfig } from "@playwright/test";

// ── Real-worker Playwright config ────────────────────────────────────────
// Runs against a live `wrangler dev --local` on :8787 seeded with L2 golden
// fixtures (see pipeline/scripts/seed_local_d1_from_fixtures.sh), so the
// browser exercises real D1 views + real Worker code paths — not the
// node http server in e2e/mock-api.ts.
//
// Scope: one dedicated smoke spec under e2e/real-worker.spec.ts. The
// existing mock-based specs make fixture-specific assertions (e.g. S&P 500
// card text, specific ticker names, "Buys by Symbol" label) that don't line
// up 1:1 with the L2 golden fixtures, so we don't try to run them here —
// the real-worker job is a complement, not a replacement.
//
// Used by .github/workflows/e2e-real-worker.yml. Runs locally too:
//   bash pipeline/scripts/seed_local_d1_from_fixtures.sh
//   (cd worker && npx wrangler dev --local) &
//   npx cross-env NEXT_PUBLIC_TIMELINE_URL=http://localhost:8787/api npx next build
//   npx cross-env PORT=3100 npx serve out --single &
//   npx playwright test --config=playwright.config.real.ts

export default defineConfig({
  testDir: "./e2e",
  testMatch: /real-worker\.spec\.ts$/,
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  // Single worker — we share one wrangler dev + one Next static server, so
  // no benefit from parallelism at this scale.
  workers: 1,
  use: {
    baseURL: "http://localhost:3100",
    serviceWorkers: "block",
  },
  // Assumes wrangler dev (:8787) was started by the calling CI job or shell
  // BEFORE the Next static build, because the build bakes
  // NEXT_PUBLIC_TIMELINE_URL into the JS bundle. We still start the Next
  // static server here via `serve` to keep the config self-contained.
  webServer: [
    {
      command: "npx cross-env PORT=3100 npx serve out --single",
      port: 3100,
      reuseExistingServer: true,
      timeout: 30_000,
    },
  ],
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
  ],
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
});
