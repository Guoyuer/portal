"use client";

// ── Gmail triage data hook ───────────────────────────────────────────────────

import { useEffect, useState } from "react";
import {
  MailListResponseSchema,
  TrashResponseSchema,
  type MailListResponse,
  type TriagedEmail,
} from "@/lib/schemas/mail";

const WORKER_URL = process.env.NEXT_PUBLIC_GMAIL_WORKER_URL ?? "";
const KEY_STORAGE = "portal:gmail:key";

function resolveKey(): string | null {
  if (typeof window === "undefined") return null;
  const url = new URL(window.location.href);
  const fromQuery = url.searchParams.get("key");
  if (fromQuery) {
    window.localStorage.setItem(KEY_STORAGE, fromQuery);
    url.searchParams.delete("key");
    window.history.replaceState(null, "", url.toString());
    return fromQuery;
  }
  return window.localStorage.getItem(KEY_STORAGE);
}

export interface UseMailState {
  loading: boolean;
  error: string | null;
  data: MailListResponse | null;
  keyMissing: boolean;
  deleteEmail: (msgId: string) => Promise<void>;
  refetch: () => void;
}

export function useMail(): UseMailState {
  const [data, setData] = useState<MailListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [keyMissing, setKeyMissing] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    const key = resolveKey();
    if (!key) {
      setKeyMissing(true);
      setLoading(false);
      return;
    }
    if (!WORKER_URL) {
      setError("NEXT_PUBLIC_GMAIL_WORKER_URL not configured");
      setLoading(false);
      return;
    }

    const ctrl = new AbortController();
    setLoading(true);
    setError(null);

    fetch(`${WORKER_URL}/mail/list`, {
      headers: { "X-Mail-Key": key },
      signal: ctrl.signal,
    })
      .then(async (r) => {
        if (r.status === 401) {
          window.localStorage.removeItem(KEY_STORAGE);
          setKeyMissing(true);
          return null;
        }
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const json = await r.json();
        return MailListResponseSchema.parse(json);
      })
      .then((parsed) => {
        if (parsed) setData(parsed);
      })
      .catch((e: unknown) => {
        if (e instanceof Error && e.name !== "AbortError") setError(e.message);
      })
      .finally(() => setLoading(false));

    return () => ctrl.abort();
  }, [refreshTick]);

  // React Compiler handles memoization for these — no useCallback.
  const deleteEmail = async (msgId: string) => {
    const key = window.localStorage.getItem(KEY_STORAGE);
    if (!key) throw new Error("no key");

    // Optimistic: drop from local state immediately.
    setData((prev) => prev && { ...prev, emails: prev.emails.filter((e) => e.msg_id !== msgId) });

    const r = await fetch(`${WORKER_URL}/mail/trash`, {
      method: "POST",
      headers: { "X-Mail-Key": key, "Content-Type": "application/json" },
      body: JSON.stringify({ msg_id: msgId }),
    });
    if (r.status === 401) {
      // Key expired or revoked — same recovery flow as the list fetch.
      window.localStorage.removeItem(KEY_STORAGE);
      setKeyMissing(true);
      throw new Error("key invalid");
    }
    const json = await r.json();
    const parsed = TrashResponseSchema.parse(json);

    if (parsed.status === "trashed" || parsed.status === "already_gone") {
      return;
    }
    // Rollback via refetch (simpler than restoring exact prior state).
    setRefreshTick((t) => t + 1);
    throw new Error(parsed.status);
  };

  const refetch = () => setRefreshTick((t) => t + 1);

  return { loading, error, data, keyMissing, deleteEmail, refetch };
}

export function groupByCategory(emails: TriagedEmail[]): {
  important: TriagedEmail[];
  neutral: TriagedEmail[];
  trash: TriagedEmail[];
} {
  return {
    important: emails.filter((e) => e.category === "IMPORTANT"),
    neutral: emails.filter((e) => e.category === "NEUTRAL"),
    trash: emails.filter((e) => e.category === "TRASH_CANDIDATE"),
  };
}
