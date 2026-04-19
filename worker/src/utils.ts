// ── Worker utility helpers ───────────────────────────────────────────────
// Pure helpers used by the main Worker entrypoint. Kept separate so tests
// can import without pulling in the default handler.

// Worker is mounted same-origin as Pages in prod (portal.guoyuer.com/api/*),
// so the browser never applies CORS. The wildcard Allow-Origin keeps cross-
// origin local dev (Next at :3000 → wrangler dev at :8787) working without
// an allowlist. Requests carry no credentials, so `*` is safe.
const RESPONSE_HEADERS: HeadersInit = {
  "Access-Control-Allow-Origin": "*",
  "Cache-Control": "no-cache",
};

/** Success JSON response. No runtime schema validation — the frontend's
 *  Zod parse in ``use-bundle.ts`` is the single source of truth for drift
 *  detection; validating twice on the same shared schema was pure CPU tax
 *  (~200ms per ``/timeline`` call on the 4.6 MB payload).
 *
 *  Optional ``init`` layers on top of the default CORS + no-cache headers so
 *  per-route overrides (e.g. ``Cache-Control: no-store`` for mutations) don't
 *  need a second helper. */
export function jsonResponse(payload: unknown, init?: ResponseInit): Response {
  const headers = new Headers(RESPONSE_HEADERS);
  if (init?.headers) new Headers(init.headers).forEach((v, k) => headers.set(k, v));
  return Response.json(payload, { ...init, headers });
}

export function dbError(e: unknown): Response {
  return Response.json(
    { error: "Database query failed", detail: e instanceof Error ? e.message : "unknown" },
    { status: 502, headers: RESPONSE_HEADERS },
  );
}

/** Caller passes a plain string and the helper wraps it in
 *  ``{ error: message }``. */
export function errorResponse(message: string, status: number): Response {
  return Response.json({ error: message }, { status, headers: RESPONSE_HEADERS });
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

interface Waiter {
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

type SettledResult<T> = { ok: true; value: T } | { ok: false; error: string };

export async function settled<T>(p: Promise<T>): Promise<SettledResult<T>> {
  try {
    return { ok: true, value: await p };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "unknown" };
  }
}

// ── sync_meta lookup ────────────────────────────────────────────────────
// Both /timeline and /econ read this key-value table; query shape is fixed.

export async function querySyncMeta(db: D1Database): Promise<Record<string, string>> {
  type KVRow = { key: string; value: string };
  const rows = await db.prepare("SELECT key, value FROM sync_meta").all<KVRow>();
  return Object.fromEntries(rows.results.map((r) => [r.key, r.value]));
}
