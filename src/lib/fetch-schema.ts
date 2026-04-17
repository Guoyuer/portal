// ── Fetch + Zod validate in one call ────────────────────────────────────
//
// The three /timeline, /econ, /prices callers all repeated the same flow:
//   1. fetch URL (with AbortSignal.timeout)
//   2. throw on non-2xx
//   3. res.json()
//   4. schema.safeParse(json)
//   5. throw on parse failure
//   6. return parsed.data
//
// This helper folds that into one call. Callers still handle the throw
// however they like (setState, toast, rethrow) — this is purely the
// shared fetch-and-validate plumbing.

import type { z } from "zod";

export class FetchSchemaError extends Error {
  constructor(message: string, readonly cause?: unknown) {
    super(message);
    this.name = "FetchSchemaError";
  }
}

export async function fetchWithSchema<T>(
  url: string,
  schema: z.ZodType<T>,
  init?: RequestInit & { timeoutMs?: number },
): Promise<T> {
  const { timeoutMs, ...rest } = init ?? {};
  const signal = timeoutMs != null ? AbortSignal.timeout(timeoutMs) : rest.signal;
  const res = await fetch(url, { ...rest, signal });
  if (!res.ok) throw new FetchSchemaError(`HTTP ${res.status} ${res.statusText}`);
  const json = await res.json();
  const parsed = schema.safeParse(json);
  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    throw new FetchSchemaError(`schema drift: ${issue?.path.join(".") ?? "root"}: ${issue?.message ?? "unknown"}`);
  }
  return parsed.data;
}
