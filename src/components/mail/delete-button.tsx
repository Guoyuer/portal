"use client";

// ── DeleteButton ─────────────────────────────────────────────────────────────

import { useState } from "react";

interface Props {
  msgId: string;
  onDelete: (msgId: string) => Promise<void>;
}

export function DeleteButton({ msgId, onDelete }: Props) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleClick = async () => {
    setBusy(true);
    setErr(null);
    try {
      await onDelete(msgId);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="inline-flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        className="rounded border border-red-200 bg-red-50 px-2 py-1 text-sm text-red-700 hover:bg-red-100 disabled:opacity-50"
      >
        {busy ? "Deleting..." : "Delete"}
      </button>
      {err && <span className="text-xs text-red-600">{err}</span>}
    </div>
  );
}
