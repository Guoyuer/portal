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

// ── Settled helper (optional queries) ────────────────────────────────────

export type SettledResult<T> = { ok: true; value: T } | { ok: false; error: string };

export async function settled<T>(p: Promise<T>): Promise<SettledResult<T>> {
  try {
    return { ok: true, value: await p };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "unknown" };
  }
}
