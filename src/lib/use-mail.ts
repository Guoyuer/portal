"use client";

// ── Gmail triage data hook ───────────────────────────────────────────────────
//
// All fetches are same-origin now (portal.guoyuer.com/api/mail/*). CF Access
// on portal.guoyuer.com already authenticated the page load, so the session
// cookie rides along automatically — no extra auth headers or URL keys.

import { useEffect, useState } from "react";
import {
  MailListResponseSchema,
  TrashResponseSchema,
  type MailListResponse,
  type TriagedEmail,
} from "@/lib/schemas/mail";

// Same-origin path. Dev override (`npm run dev` against a local worker-gmail)
// can still point elsewhere via NEXT_PUBLIC_GMAIL_WORKER_URL.
const MAIL_BASE = process.env.NEXT_PUBLIC_GMAIL_WORKER_URL ?? "/api/mail";

interface UseMailState {
  loading: boolean;
  error: string | null;
  data: MailListResponse | null;
  deleteEmail: (msgId: string) => Promise<void>;
  refetch: () => void;
}

export function useMail(): UseMailState {
  const [data, setData] = useState<MailListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);

    fetch(`${MAIL_BASE}/list`, { signal: ctrl.signal })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const json = await r.json();
        return MailListResponseSchema.parse(json);
      })
      .then((parsed) => setData(parsed))
      .catch((e: unknown) => {
        if (e instanceof Error && e.name !== "AbortError") setError(e.message);
      })
      .finally(() => setLoading(false));

    return () => ctrl.abort();
  }, [refreshTick]);

  // React Compiler handles memoization for these — no useCallback.
  const deleteEmail = async (msgId: string) => {
    // Optimistic: drop from local state immediately.
    setData((prev) => prev && { ...prev, emails: prev.emails.filter((e) => e.msg_id !== msgId) });

    const r = await fetch(`${MAIL_BASE}/trash`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ msg_id: msgId }),
    });
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

  return { loading, error, data, deleteEmail, refetch };
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
