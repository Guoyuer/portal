import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    "coverage/**",
    "test-results/**",
    ".pytest_cache/**",
    ".ruff_cache/**",
    ".mypy_cache/**",
    ".wrangler/**",
    "worker/.wrangler/",
    "worker/.wrangler/**",
    ".codex/**",
    "pipeline/.venv/",
    "pipeline/.venv/**",
    "pipeline/.pytest-tmp*/",
    "pipeline/.pytest-tmp*/**",
  ]),
]);

export default eslintConfig;
