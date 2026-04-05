import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  retries: 0,
  workers: process.env.CI ? 4 : undefined,
  use: {
    baseURL: "http://localhost:3100",
  },
  webServer: {
    command: "npx cross-env PORT=3100 npx serve out --single",
    port: 3100,
    reuseExistingServer: true,
  },
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
  ],
});
