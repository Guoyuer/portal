"use client";

// ── MailRow ──────────────────────────────────────────────────────────────────

import type { TriagedEmail } from "@/lib/schemas/mail";
import { DeleteButton } from "@/components/mail/delete-button";

interface Props {
  email: TriagedEmail;
  onDelete?: (msgId: string) => Promise<void>;
}

export function MailRow({ email, onDelete }: Props) {
  const gmailLink = `https://mail.google.com/mail/u/0/#inbox/${encodeURIComponent(email.msg_id)}`;
  const canDelete = email.category === "TRASH_CANDIDATE" && onDelete;

  return (
    <div className="flex items-start justify-between gap-3 border-b border-gray-100 py-2 last:border-b-0">
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-gray-900">{email.subject || "(no subject)"}</div>
        <div className="truncate text-xs text-gray-500">{email.sender}</div>
        {email.summary && <div className="mt-1 text-xs text-gray-700">{email.summary}</div>}
      </div>
      <div className="flex flex-shrink-0 flex-col items-end gap-1">
        <a href={gmailLink} target="_blank" rel="noreferrer" className="text-xs text-blue-600 underline">
          Open in Gmail
        </a>
        {canDelete && <DeleteButton msgId={email.msg_id} onDelete={onDelete!} />}
      </div>
    </div>
  );
}
