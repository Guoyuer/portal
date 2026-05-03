import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : 4,
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
