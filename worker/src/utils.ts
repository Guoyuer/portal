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
  // `caches.default` is the Cloudflare-provided global cache — typed as
  // `Cache` by @cloudflare/workers-types, which is a superset of `CacheLike`.
  const cache: CacheLike = cacheOverride ?? caches.default;
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
