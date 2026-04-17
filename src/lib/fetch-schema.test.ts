// @vitest-environment jsdom

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { z } from "zod";
import { fetchWithSchema, FetchSchemaError } from "./fetch-schema";

const Shape = z.object({ ok: z.boolean(), n: z.number() });

describe("fetchWithSchema", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => vi.unstubAllGlobals());

  const mock = (body: unknown, ok = true, status = 200, statusText = "") =>
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok,
      status,
      statusText,
      json: async () => body,
    });

  it("returns parsed data on 2xx + valid shape", async () => {
    mock({ ok: true, n: 42 });
    await expect(fetchWithSchema("/x", Shape)).resolves.toEqual({ ok: true, n: 42 });
  });

  it("throws FetchSchemaError with HTTP status + text on non-2xx", async () => {
    mock({}, false, 503, "Service Unavailable");
    await expect(fetchWithSchema("/x", Shape)).rejects.toSatisfy((e: unknown) => {
      if (!(e instanceof FetchSchemaError)) return false;
      return /HTTP 5\d\d/.test(e.message) && /Service Unavailable/.test(e.message);
    });
  });

  it("throws FetchSchemaError with 'schema drift' prefix on parse failure", async () => {
    mock({ ok: "not a bool", n: 42 });
    await expect(fetchWithSchema("/x", Shape)).rejects.toThrow(/^schema drift:/);
  });

  it("passes through RequestInit options (cache, headers)", async () => {
    mock({ ok: true, n: 1 });
    await fetchWithSchema("/x", Shape, { cache: "no-store", headers: { "X-Test": "1" } });
    const call = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[1].cache).toBe("no-store");
    expect((call[1].headers as Record<string, string>)["X-Test"]).toBe("1");
  });

  it("creates an AbortSignal when timeoutMs is provided", async () => {
    mock({ ok: true, n: 1 });
    await fetchWithSchema("/x", Shape, { timeoutMs: 5_000 });
    const call = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[1].signal).toBeInstanceOf(AbortSignal);
  });
});
