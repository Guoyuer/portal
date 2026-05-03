import { existsSync, readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";

import { EconDataSchema } from "../src/lib/schemas/econ";
import { TickerPricesBundleSchema } from "../src/lib/schemas/ticker";
import { TimelineDataSchema } from "../src/lib/schemas/timeline";

const ACCESS_ID_ENV = "CLOUDFLARE_ACCESS_CLIENT_ID";
const ACCESS_SECRET_ENV = "CLOUDFLARE_ACCESS_CLIENT_SECRET";
const LOG_PREFIX = "[validate_api_zod]";

type ManifestObject = {
  key: string;
};

type R2Manifest = {
  version: string;
  objects: {
    timeline: ManifestObject;
    econ: ManifestObject;
    prices: ManifestObject;
  };
};

type Payloads = {
  timeline: unknown;
  econ: unknown;
  prices: unknown;
  version?: string;
};

function loadAccessEnvFile(): void {
  if (process.env[ACCESS_ID_ENV] && process.env[ACCESS_SECRET_ENV]) return;

  const envPath = "worker/.env.access";
  if (!existsSync(envPath)) return;

  for (const line of readFileSync(envPath, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const index = trimmed.indexOf("=");
    if (index <= 0) continue;
    process.env[trimmed.slice(0, index).trim()] ??= trimmed.slice(index + 1).trim();
  }
}

function accessHeaders(): HeadersInit {
  loadAccessEnvFile();
  const clientId = process.env[ACCESS_ID_ENV];
  const clientSecret = process.env[ACCESS_SECRET_ENV];
  if (!clientId && !clientSecret) return {};
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

async function fetchJson(url: string, headers: HeadersInit): Promise<unknown> {
  const res = await fetch(url, { cache: "no-store", headers });
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

async function readJson<T>(filePath: string): Promise<T> {
  return JSON.parse(await readFile(filePath, "utf8")) as T;
}

function artifactPath(root: string, key: string): string {
  return path.join(root, ...key.split("/"));
}

function fail(
  label: string,
  result: { success: false; error: { issues: Array<{ path: Array<string | number>; message: string }> } },
): never {
  const issue = result.error.issues[0];
  const issuePath = issue?.path.join(".") || "<root>";
  console.error(`${LOG_PREFIX} ${label} schema drift at ${issuePath}: ${issue?.message ?? "unknown"}`);
  console.error(`${LOG_PREFIX} ${label} total issues: ${result.error.issues.length}`);
  process.exit(1);
}

function validate(payloads: Payloads): void {
  const timeline = TimelineDataSchema.safeParse(payloads.timeline);
  if (!timeline.success) fail("timeline", timeline);

  const econ = EconDataSchema.safeParse(payloads.econ);
  if (!econ.success) fail("econ", econ);

  const prices = TickerPricesBundleSchema.safeParse(payloads.prices);
  if (!prices.success) fail("prices", prices);

  const pricePayloads = Object.values(prices.data);
  const priceRows = pricePayloads.reduce((sum, payload) => sum + payload.prices.length, 0);
  const transactionRows = pricePayloads.reduce((sum, payload) => sum + payload.transactions.length, 0);
  const version = payloads.version ? ` version=${payloads.version}` : "";
  console.log(
    `${LOG_PREFIX} ok${version} - ${timeline.data.daily.length} daily rows, `
      + `${timeline.data.dailyTickers.length} ticker rows, `
      + `${Object.keys(econ.data.series).length} econ series, `
      + `${pricePayloads.length} price symbols, ${priceRows} price rows, `
      + `${transactionRows} transaction rows`,
  );
}

async function loadArtifactPayloads(artifactDir: string): Promise<Payloads> {
  const manifest = await readJson<R2Manifest>(path.join(artifactDir, "manifest.json"));
  return {
    timeline: await readJson<unknown>(artifactPath(artifactDir, manifest.objects.timeline.key)),
    econ: await readJson<unknown>(artifactPath(artifactDir, manifest.objects.econ.key)),
    prices: await readJson<unknown>(artifactPath(artifactDir, manifest.objects.prices.key)),
    version: manifest.version,
  };
}

async function loadLivePayloads(): Promise<Payloads> {
  const timelineUrl = process.env.TIMELINE_URL ?? "http://localhost:8787/timeline";
  const headers = accessHeaders();
  return {
    timeline: await fetchJson(timelineUrl, headers),
    econ: await fetchJson(process.env.ECON_URL ?? siblingUrl(timelineUrl, "econ"), headers),
    prices: await fetchJson(process.env.PRICES_URL ?? siblingUrl(timelineUrl, "prices"), headers),
  };
}

async function main(): Promise<void> {
  const mode = process.argv[2] ?? "live";
  if (mode === "artifacts") {
    validate(await loadArtifactPayloads(process.argv[3] ?? path.join("pipeline", "artifacts", "r2")));
    return;
  }
  if (mode === "live") {
    validate(await loadLivePayloads());
    return;
  }
  console.error(`${LOG_PREFIX} usage: tsx scripts/validate_api_zod.ts [live|artifacts <artifactDir>]`);
  process.exit(1);
}

main().catch((err) => {
  console.error(`${LOG_PREFIX} unexpected error: ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
});
