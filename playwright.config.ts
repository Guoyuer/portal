import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  retries: 0,
  workers: process.env.CI ? 4 : undefined,
  use: {
    baseURL: "http://localhost:3000",
  },
  webServer: {
    command: "npx serve out -l 3000 --single",
    port: 3000,
    reuseExistingServer: true,
  },
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
  ],
});
