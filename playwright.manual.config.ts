import { defineConfig } from "@playwright/test";

// Throwaway config for running manual/* screenshot specs against the running dev server.
// Does NOT start a web server — expects dev (3000) + worker (8787) already up.
export default defineConfig({
  testDir: "./e2e/manual",
  use: {
    baseURL: "http://localhost:3000",
    serviceWorkers: "block",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  reporter: "line",
});
