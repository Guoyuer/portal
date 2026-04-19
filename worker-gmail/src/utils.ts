// ── Worker-gmail response helpers ────────────────────────────────────────
// Kept intentionally minimal (three endpoints, no caching layer). Function
// signatures mirror ``worker/src/utils.ts`` so call sites read the same in
// either Worker; the two tsconfigs don't cross-reference, so the bodies are
// hand-maintained clones rather than a shared import.

/** Signature mirrors ``worker/src/utils.ts::jsonResponse`` — optional
 *  ``init`` lets routes layer on per-response headers (``/api/mail/list``
 *  sets ``Cache-Control: no-store`` to force a re-fetch after trash). */
export function jsonResponse(payload: unknown, init?: ResponseInit): Response {
  return Response.json(payload, init);
}

export function errorResponse(message: string, status: number): Response {
  return Response.json({ error: message }, { status });
}

export function statusResponse(status: string, httpStatus = 200): Response {
  return Response.json({ status }, { status: httpStatus });
}

export function notFoundResponse(): Response {
  return new Response("Not found", { status: 404 });
}

/** Parse the request body as JSON or return a 400 Response. */
export async function parseJsonBody(request: Request): Promise<unknown | Response> {
  try {
    return await request.json();
  } catch {
    return errorResponse("invalid json", 400);
  }
}
