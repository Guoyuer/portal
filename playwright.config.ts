import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  // e2e/manual/ holds interactive debug tools (screenshot-capturing specs);
  // run explicitly with `npx playwright test e2e/manual/*.spec.ts --headed`.
  // real-worker.spec.ts assumes a live wrangler dev on :8787 seeded from L2
  // fixtures — see playwright.config.real.ts + the e2e-real-worker.yml
  // workflow; it would fail against the mock-API server this config boots.
  testIgnore: [/manual\//, /real-worker\.spec\.ts$/],
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  use: {
    baseURL: "http://localhost:3100",
    serviceWorkers: "block",
  },
  webServer: [
    {
      command: "npx tsx e2e/mock-api.ts",
      port: 4444,
      reuseExistingServer: true,
    },
    {
      command: "npx cross-env NEXT_PUBLIC_TIMELINE_URL=http://localhost:4444 npx next build && npx cross-env PORT=3100 npx serve out --single",
      port: 3100,
      reuseExistingServer: true,
      timeout: 60_000,
    },
  ],
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
  ],
});
