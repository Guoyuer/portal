// ── CI smoke: live Worker payloads vs. frontend Zod schemas ─────────────
//
// Worker is a thin R2 object-streaming facade — it does not validate at
// runtime. This script runs the same frontend Zod schemas against a live
// Worker, so artifact/schema drift breaks CI before browser rendering.
//
// Invoked from .github/workflows/e2e-real-worker.yml AFTER wrangler is
// up and BEFORE Playwright. A Zod failure here exits non-zero with a
// readable path.message, which is much clearer than a downstream
// Playwright "undefined is not an object" at render time.
//
// Run locally against a local Worker:
//   bash pipeline/scripts/seed_local_r2_from_fixtures.sh
//   (cd worker && npx wrangler dev --local) &
//   npm run validate:api
//
// Run against production:
//   TIMELINE_URL=https://portal.guoyuer.com/api/timeline npm run validate:api
//
// Env: TIMELINE_URL (default http://localhost:8787/timeline). ECON_URL and
// PRICES_URL can override the inferred sibling endpoints. If CF Access
// service-token credentials are set, or worker/.env.access exists, the script
// sends the required headers.

import { existsSync, readFileSync } from "node:fs";
import { EconDataSchema } from "../src/lib/schemas/econ";
import { TickerPricesBundleSchema } from "../src/lib/schemas/ticker";
import { TimelineDataSchema } from "../src/lib/schemas/timeline";

const ACCESS_ID_ENV = "CLOUDFLARE_ACCESS_CLIENT_ID";
const ACCESS_SECRET_ENV = "CLOUDFLARE_ACCESS_CLIENT_SECRET";
const LOG_PREFIX = "[validate_live_api_zod]";

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
    console.error(`${LOG_PREFIX} ${ACCESS_ID_ENV} and ${ACCESS_SECRET_ENV} must be set together`);
    process.exit(1);
  }

  return {
    "CF-Access-Client-Id": clientId,
    "CF-Access-Client-Secret": clientSecret,
  };
}

function siblingUrl(timelineUrl: string, endpoint: "econ" | "prices"): string {
  return timelineUrl.replace(/\/timeline(?:\?.*)?$/, `/${endpoint}`);
}

async function fetchJson(url: string): Promise<unknown> {
  const res = await fetch(url, { cache: "no-store", headers: accessHeaders() });
  const body = await res.text();
  if (!res.ok) {
    console.error(`${LOG_PREFIX} HTTP ${res.status} ${res.statusText} from ${url}`);
    process.exit(1);
  }

  const contentType = res.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("application/json")) {
    console.error(`${LOG_PREFIX} expected JSON from ${url}, got ${contentType || "<missing content-type>"}`);
    console.error(`${LOG_PREFIX} body starts with: ${body.slice(0, 120).replace(/\s+/g, " ")}`);
    process.exit(1);
  }

  return JSON.parse(body);
}

function fail(label: string, result: { success: false; error: { issues: Array<{ path: Array<string | number>; message: string }> } }): never {
  const issue = result.error.issues[0];
  const path = issue?.path.join(".") || "<root>";
  console.error(`${LOG_PREFIX} ${label} schema drift at ${path}: ${issue?.message ?? "unknown"}`);
  console.error(`${LOG_PREFIX} ${label} total issues: ${result.error.issues.length}`);
  process.exit(1);
}

async function main(): Promise<void> {
  const timelineUrl = process.env.TIMELINE_URL ?? "http://localhost:8787/timeline";
  const econUrl = process.env.ECON_URL ?? siblingUrl(timelineUrl, "econ");
  const pricesUrl = process.env.PRICES_URL ?? siblingUrl(timelineUrl, "prices");

  const timelineParsed = TimelineDataSchema.safeParse(await fetchJson(timelineUrl));
  if (!timelineParsed.success) fail("timeline", timelineParsed);

  const econParsed = EconDataSchema.safeParse(await fetchJson(econUrl));
  if (!econParsed.success) fail("econ", econParsed);

  const pricesParsed = TickerPricesBundleSchema.safeParse(await fetchJson(pricesUrl));
  if (!pricesParsed.success) fail("prices", pricesParsed);

  const pricePayloads = Object.values(pricesParsed.data);
  const priceRows = pricePayloads.reduce((sum, payload) => sum + payload.prices.length, 0);
  console.log(
    `${LOG_PREFIX} ok — ${timelineParsed.data.daily.length} daily rows, `
    + `${timelineParsed.data.dailyTickers.length} ticker rows, `
    + `${Object.keys(econParsed.data.series).length} econ series, `
    + `${pricePayloads.length} price symbols, ${priceRows} price rows`,
  );
}

main().catch((err) => {
  console.error(`${LOG_PREFIX} unexpected error: ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
});
