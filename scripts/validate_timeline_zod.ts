// ── CI smoke: Worker /timeline vs. frontend Zod schema ──────────────────
//
// Worker is a thin R2 object-streaming facade — it does not validate at
// runtime. This script runs the same `TimelineDataSchema.safeParse` the
// browser runs, against a live `wrangler dev --local` seeded from L2
// fixtures, so artifact/schema drift breaks CI instead.
//
// Invoked from .github/workflows/e2e-real-worker.yml AFTER wrangler is
// up and BEFORE Playwright. A Zod failure here exits non-zero with a
// readable path.message, which is much clearer than a downstream
// Playwright "undefined is not an object" at render time.
//
// Run locally against a local Worker:
//   bash pipeline/scripts/seed_local_r2_from_fixtures.sh
//   (cd worker && npx wrangler dev --local) &
//   npx tsx scripts/validate_timeline_zod.ts
//
// Run against production:
//   TIMELINE_URL=https://portal.guoyuer.com/api/timeline npx tsx scripts/validate_timeline_zod.ts
//
// Env: TIMELINE_URL (default http://localhost:8787/timeline). If
// CLOUDFLARE_ACCESS_CLIENT_ID / CLOUDFLARE_ACCESS_CLIENT_SECRET are set, or
// worker/.env.access exists, the script sends CF Access service-token headers.

import { existsSync, readFileSync } from "node:fs";
import { TimelineDataSchema } from "../src/lib/schemas/timeline";

const ACCESS_ID_ENV = "CLOUDFLARE_ACCESS_CLIENT_ID";
const ACCESS_SECRET_ENV = "CLOUDFLARE_ACCESS_CLIENT_SECRET";

function loadAccessEnvFile(): void {
  if (process.env[ACCESS_ID_ENV] && process.env[ACCESS_SECRET_ENV]) {
    return;
  }

  const path = "worker/.env.access";
  if (!existsSync(path)) {
    return;
  }

  const lines = readFileSync(path, "utf8").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }
    const index = trimmed.indexOf("=");
    if (index <= 0) {
      continue;
    }
    const key = trimmed.slice(0, index).trim();
    const value = trimmed.slice(index + 1).trim();
    process.env[key] ??= value;
  }
}

function accessHeaders(): HeadersInit {
  loadAccessEnvFile();

  const clientId = process.env[ACCESS_ID_ENV];
  const clientSecret = process.env[ACCESS_SECRET_ENV];
  if (!clientId && !clientSecret) {
    return {};
  }
  if (!clientId || !clientSecret) {
    console.error(`[validate_timeline_zod] ${ACCESS_ID_ENV} and ${ACCESS_SECRET_ENV} must be set together`);
    process.exit(1);
  }

  return {
    "CF-Access-Client-Id": clientId,
    "CF-Access-Client-Secret": clientSecret,
  };
}

async function main(): Promise<void> {
  const url = process.env.TIMELINE_URL ?? "http://localhost:8787/timeline";

  const res = await fetch(url, { cache: "no-store", headers: accessHeaders() });
  const body = await res.text();
  if (!res.ok) {
    console.error(`[validate_timeline_zod] HTTP ${res.status} ${res.statusText} from ${url}`);
    process.exit(1);
  }

  const contentType = res.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("application/json")) {
    console.error(`[validate_timeline_zod] expected JSON from ${url}, got ${contentType || "<missing content-type>"}`);
    console.error(`[validate_timeline_zod] body starts with: ${body.slice(0, 120).replace(/\s+/g, " ")}`);
    process.exit(1);
  }

  const payload = JSON.parse(body);
  const parsed = TimelineDataSchema.safeParse(payload);

  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    const path = issue?.path.join(".") || "<root>";
    console.error(`[validate_timeline_zod] schema drift at ${path}: ${issue?.message ?? "unknown"}`);
    console.error(`[validate_timeline_zod] total issues: ${parsed.error.issues.length}`);
    process.exit(1);
  }

  console.log(`[validate_timeline_zod] ok — ${parsed.data.daily.length} daily rows, ${parsed.data.dailyTickers.length} ticker rows`);
}

main().catch((err) => {
  console.error(`[validate_timeline_zod] unexpected error: ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
});
