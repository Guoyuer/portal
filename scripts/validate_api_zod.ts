import { readFile } from "node:fs/promises";
import path from "node:path";

import { EconDataSchema } from "../src/lib/schemas/econ";
import { TickerPricesBundleSchema } from "../src/lib/schemas/ticker";
import { TimelineDataSchema } from "../src/lib/schemas/timeline";

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

async function main(): Promise<void> {
  const [artifactDir = path.join("pipeline", "artifacts", "r2"), extra] = process.argv.slice(2);
  if (extra) {
    console.error(`${LOG_PREFIX} usage: tsx scripts/validate_api_zod.ts [artifactDir]`);
    process.exit(1);
  }
  validate(await loadArtifactPayloads(artifactDir));
}

main().catch((err) => {
  console.error(`${LOG_PREFIX} unexpected error: ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
});
