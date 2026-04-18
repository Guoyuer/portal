// @vitest-environment jsdom

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useMail, groupByCategory } from "@/lib/hooks/use-mail";
import type { TriagedEmail } from "@/lib/schemas/mail";

// ── Test fixtures ────────────────────────────────────────────────────────
const emails: TriagedEmail[] = [
  { msg_id: "a", sender: "news@x.com", subject: "Breaking", summary: "Big news", category: "IMPORTANT" },
  { msg_id: "b", sender: "marketing@y.com", subject: "Sale", summary: "50% off", category: "NEUTRAL" },
  { msg_id: "c", sender: "spam@z.com", subject: "Claim your prize", summary: "You won", category: "TRASH_CANDIDATE" },
];

// ── groupByCategory (pure) ───────────────────────────────────────────────
describe("groupByCategory", () => {
  it("splits emails by category", () => {
    const groups = groupByCategory(emails);
    expect(groups.important).toHaveLength(1);
    expect(groups.important[0].msg_id).toBe("a");
    expect(groups.neutral).toHaveLength(1);
    expect(groups.neutral[0].msg_id).toBe("b");
    expect(groups.trash).toHaveLength(1);
    expect(groups.trash[0].msg_id).toBe("c");
  });

  it("returns empty arrays for missing categories", () => {
    const groups = groupByCategory([]);
    expect(groups.important).toEqual([]);
    expect(groups.neutral).toEqual([]);
    expect(groups.trash).toEqual([]);
  });
});

// ── useMail hook — fetch lifecycle + optimistic delete ───────────────────
//
// The fetch mock is a URL-dispatched router: each call consumes the first
// entry of the per-endpoint queue, or a default. This avoids the ordering
// traps of a single global `mockResolvedValueOnce` queue (easy to drain
// unexpectedly when React schedules an extra effect run).
type FakeResponse = { ok?: boolean; status?: number; body: unknown };
describe("useMail", () => {
  const listQueue: FakeResponse[] = [];
  const trashQueue: FakeResponse[] = [];
  let listDefault: FakeResponse = { body: { emails: [], as_of: "2026-01-01T00:00:00Z" } };

  beforeEach(() => {
    listQueue.length = 0;
    trashQueue.length = 0;
    listDefault = { body: { emails: [], as_of: "2026-01-01T00:00:00Z" } };

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const urlStr = String(url);
        let pick: FakeResponse;
        if (urlStr.endsWith("/mail/list")) {
          pick = listQueue.shift() ?? listDefault;
        } else if (urlStr.endsWith("/mail/trash")) {
          pick = trashQueue.shift() ?? { body: { status: "error" } };
        } else {
          throw new Error(`unexpected fetch url: ${urlStr}`);
        }
        return {
          ok: pick.ok ?? true,
          status: pick.status ?? 200,
          json: async () => pick.body,
        } as Response;
      }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("fetches and returns the parsed list", async () => {
    listDefault = { body: { emails, as_of: "2026-04-01T00:00:00Z" } };
    const { result } = renderHook(() => useMail());

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBeNull();
    expect(result.current.data?.emails).toHaveLength(3);
  });

  it("surfaces HTTP error as error string (not thrown)", async () => {
    listDefault = { ok: false, status: 503, body: {} };
    const { result } = renderHook(() => useMail());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("HTTP 503");
    expect(result.current.data).toBeNull();
  });

  it("surfaces schema validation error", async () => {
    // emails missing → ZodError
    listDefault = { body: { as_of: "2026-04-01T00:00:00Z" } };
    const { result } = renderHook(() => useMail());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBeTruthy();
    expect(result.current.data).toBeNull();
  });

  it("optimistically removes on deleteEmail and keeps it gone on success", async () => {
    listDefault = { body: { emails, as_of: "2026-04-01T00:00:00Z" } };
    const { result } = renderHook(() => useMail());
    await waitFor(() => expect(result.current.data?.emails).toHaveLength(3));

    trashQueue.push({ body: { status: "trashed" } });
    await act(async () => {
      await result.current.deleteEmail("b");
    });
    expect(result.current.data?.emails.map((e) => e.msg_id)).toEqual(["a", "c"]);
  });

  it("treats 'already_gone' as success (no rollback)", async () => {
    listDefault = { body: { emails, as_of: "2026-04-01T00:00:00Z" } };
    const { result } = renderHook(() => useMail());
    await waitFor(() => expect(result.current.data?.emails).toHaveLength(3));

    trashQueue.push({ body: { status: "already_gone" } });
    await act(async () => {
      await result.current.deleteEmail("a");
    });
    expect(result.current.data?.emails.map((e) => e.msg_id)).toEqual(["b", "c"]);
  });

  it("propagates the trash error to the caller when the server rejects", async () => {
    listDefault = { body: { emails, as_of: "2026-04-01T00:00:00Z" } };
    const { result } = renderHook(() => useMail());
    await waitFor(() => expect(result.current.data?.emails).toHaveLength(3));

    trashQueue.push({ body: { status: "auth_failed" } });

    await expect(
      act(async () => {
        await result.current.deleteEmail("a");
      }),
    ).rejects.toThrow("auth_failed");
  });

  it("refetch() bumps the request counter and re-pulls the list", async () => {
    listDefault = { body: { emails, as_of: "2026-04-01T00:00:00Z" } };
    const { result } = renderHook(() => useMail());
    await waitFor(() => expect(result.current.data?.as_of).toBe("2026-04-01T00:00:00Z"));

    listQueue.push({ body: { emails, as_of: "2026-04-02T00:00:00Z" } });
    act(() => result.current.refetch());
    await waitFor(() => expect(result.current.data?.as_of).toBe("2026-04-02T00:00:00Z"));
  });
});
