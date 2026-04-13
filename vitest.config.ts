import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  test: {
    include: ["src/**/*.test.{ts,tsx}", "worker/src/**/*.test.ts", "worker-gmail/src/**/*.test.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary", "json"],
      include: ["src/lib/**"],
      exclude: [
        "src/lib/chart-styles.ts",
        "src/lib/hooks.ts",
        // use-mail is integration-heavy (fetch + localStorage + Next.js
        // router). Covered by the /mail e2e flow, not unit tests.
        "src/lib/use-mail.ts",
      ],
      thresholds: {
        statements: 70,
        branches: 70,
        functions: 70,
        lines: 70,
      },
    },
  },
});
