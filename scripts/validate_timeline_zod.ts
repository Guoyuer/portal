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
// Run locally:
//   bash pipeline/scripts/seed_local_r2_from_fixtures.sh
//   (cd worker && npx wrangler dev --local) &
//   npx tsx scripts/validate_timeline_zod.ts
//
// Env: TIMELINE_URL (default http://localhost:8787/timeline).

import { TimelineDataSchema } from "../src/lib/schemas/timeline";

async function main(): Promise<void> {
  const url = process.env.TIMELINE_URL ?? "http://localhost:8787/timeline";

  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    console.error(`[validate_timeline_zod] HTTP ${res.status} ${res.statusText} from ${url}`);
    process.exit(1);
  }

  const payload = await res.json();
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
