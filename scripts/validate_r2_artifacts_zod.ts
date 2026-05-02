// ── R2 artifact schema validation ───────────────────────────────────────
//
// Offline companion to the browser's runtime Zod checks. The Python exporter
// writes endpoint-shaped JSON; this script proves those files still parse with
// the same schemas used by the frontend.

import { readFile } from "node:fs/promises";
import path from "node:path";

import { EconDataSchema } from "../src/lib/schemas/econ";
import { TickerPriceResponseSchema } from "../src/lib/schemas/ticker";
import { TimelineDataSchema } from "../src/lib/schemas/timeline";

type ManifestObject = {
  key: string;
  sha256: string;
  bytes: number;
  contentType: string;
};

type R2Manifest = {
  version: string;
  objects: {
    timeline: ManifestObject;
    econ: ManifestObject;
  };
  prices: Record<string, ManifestObject>;
};

async function readJson<T>(filePath: string): Promise<T> {
  const raw = await readFile(filePath, "utf8");
  return JSON.parse(raw) as T;
}

function artifactPath(root: string, key: string): string {
  return path.join(root, ...key.split("/"));
}

function fail(label: string, result: { success: false; error: { issues: Array<{ path: Array<string | number>; message: string }> } }): never {
  const issue = result.error.issues[0];
  const issuePath = issue?.path.join(".") || "<root>";
  console.error(`[validate_r2_artifacts_zod] ${label} schema drift at ${issuePath}: ${issue?.message ?? "unknown"}`);
  console.error(`[validate_r2_artifacts_zod] ${label} total issues: ${result.error.issues.length}`);
  process.exit(1);
}

async function main(): Promise<void> {
  const artifactDir = process.argv[2] ?? path.join("pipeline", "artifacts", "r2");
  const manifest = await readJson<R2Manifest>(path.join(artifactDir, "manifest.json"));

  const timeline = await readJson<unknown>(artifactPath(artifactDir, manifest.objects.timeline.key));
  const timelineParsed = TimelineDataSchema.safeParse(timeline);
  if (!timelineParsed.success) fail("timeline", timelineParsed);

  const econ = await readJson<unknown>(artifactPath(artifactDir, manifest.objects.econ.key));
  const econParsed = EconDataSchema.safeParse(econ);
  if (!econParsed.success) fail("econ", econParsed);

  let priceRows = 0;
  let transactionRows = 0;
  for (const [symbol, descriptor] of Object.entries(manifest.prices)) {
    const payload = await readJson<unknown>(artifactPath(artifactDir, descriptor.key));
    const parsed = TickerPriceResponseSchema.safeParse(payload);
    if (!parsed.success) fail(`prices/${symbol}`, parsed);
    priceRows += parsed.data.prices.length;
    transactionRows += parsed.data.transactions.length;
  }

  console.log(
    `[validate_r2_artifacts_zod] ok - version=${manifest.version} `
      + `daily=${timelineParsed.data.daily.length} prices=${Object.keys(manifest.prices).length} `
      + `priceRows=${priceRows} transactionRows=${transactionRows}`,
  );
}

main().catch((err) => {
  console.error(`[validate_r2_artifacts_zod] unexpected error: ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
});
