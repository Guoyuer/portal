// ── Worker utility helpers ───────────────────────────────────────────────
// Pure helpers used by the main Worker entrypoint. Kept separate so tests
// can import without pulling in the default handler.

// Worker is mounted same-origin as Pages in prod (portal.guoyuer.com/api/*),
// so the browser never applies CORS. The wildcard Allow-Origin keeps cross-
// origin local dev (Next at :3000 → wrangler dev at :8787) working without
// an allowlist. Requests carry no credentials, so `*` is safe.
const RESPONSE_HEADERS: HeadersInit = {
  "Access-Control-Allow-Origin": "*",
  "Cache-Control": "no-store",
};

/** Caller passes a plain string and the helper wraps it in
 *  ``{ error: message }``. */
export function errorResponse(message: string, status: number): Response {
  return Response.json({ error: message }, { status, headers: RESPONSE_HEADERS });
}

export function notFoundResponse(): Response {
  return new Response("Not found", { status: 404, headers: RESPONSE_HEADERS });
}
