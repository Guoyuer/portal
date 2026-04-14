// ── Worker utility helpers ───────────────────────────────────────────────
// Pure helpers used by the main Worker entrypoint. Kept separate so tests
// can import without pulling in the default handler.

import type { z } from "zod";

// Worker is mounted same-origin as Pages in prod (portal.guoyuer.com/api/*),
// so the browser never applies CORS. The wildcard Allow-Origin keeps cross-
// origin local dev (Next at :3000 → wrangler dev at :8787) working without
// an allowlist. Requests carry no credentials, so `*` is safe.
const RESPONSE_HEADERS: HeadersInit = {
  "Access-Control-Allow-Origin": "*",
  "Cache-Control": "no-cache",
};

export function validatedResponse<T>(
  schema: z.ZodType<T>,
  payload: unknown,
): Response {
  const parsed = schema.safeParse(payload);
  if (!parsed.success) {
    const detail = parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ");
    return Response.json(
      { error: "schema drift", detail },
      { status: 500, headers: RESPONSE_HEADERS },
    );
  }
  return Response.json(parsed.data, { headers: RESPONSE_HEADERS });
}

export function dbError(e: unknown): Response {
  return Response.json(
    { error: "Database query failed", detail: e instanceof Error ? e.message : "unknown" },
    { status: 502, headers: RESPONSE_HEADERS },
  );
}

export function errorResponse(body: unknown, status: number): Response {
  return Response.json(body, { status, headers: RESPONSE_HEADERS });
}

export function notFoundResponse(): Response {
  return new Response("Not found", { status: 404, headers: RESPONSE_HEADERS });
}

// ── Edge cache wrapper ──────────────────────────────────────────────────
//
// The D1 free tier is 5M row-reads / 100k row-writes per day, and
// `GET /timeline` alone reads ~36k rows per call (v_daily_tickers
// dominates). A day of iteration can trivially cross the ceiling once;
// real telemetry showed 119M row-reads on a single dev-heavy day. An
// edge cache in front of the read-only GETs turns repeat requests
// within the TTL into zero D1 reads.
//
// We own the Cache-Control header here (it overrides the producer's
// default "no-cache"). 2xx only — errors and 503s bypass the cache so a
// transient failure doesn't freeze.

export interface Waiter {
  waitUntil(promise: Promise<unknown>): void;
}

export interface CacheLike {
  match(request: Request): Promise<Response | undefined>;
  put(request: Request, response: Response): Promise<void>;
}

export async function cachedJson(
  request: Request,
  ctx: Waiter,
  ttlSeconds: number,
  produce: () => Promise<Response>,
  cacheOverride?: CacheLike,
): Promise<Response> {
  // Cache key: normalize to GET on the full URL so method/header variance
  // doesn't shard the key. Mutations (POST, etc.) never reach this path
  // — they route past /timeline, /econ, /prices in the handler.
  const cache = cacheOverride ?? (caches as unknown as { default: CacheLike }).default;
  const key = new Request(new URL(request.url).toString(), { method: "GET" });

  const hit = await cache.match(key);
  if (hit) return hit;

  const fresh = await produce();
  if (!fresh.ok) return fresh;

  // Re-emit with the cache TTL header overriding the producer's default.
  const headers = new Headers(fresh.headers);
  headers.set("Cache-Control", `public, max-age=${ttlSeconds}`);
  const cacheable = new Response(fresh.body, { status: fresh.status, headers });

  ctx.waitUntil(cache.put(key, cacheable.clone()));
  return cacheable;
}


// ── Settled helper (optional queries) ────────────────────────────────────

export type SettledResult<T> = { ok: true; value: T } | { ok: false; error: string };

export async function settled<T>(p: Promise<T>): Promise<SettledResult<T>> {
  try {
    return { ok: true, value: await p };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "unknown" };
  }
}
