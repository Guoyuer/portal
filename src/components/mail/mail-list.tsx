"use client";

// ── MailSection ──────────────────────────────────────────────────────────────

import type { TriagedEmail } from "@/lib/schemas/mail";
import { MailRow } from "@/components/mail/mail-row";
import { SectionMessage } from "@/components/finance/section";

interface Props {
  title: string;
  emoji: string;
  emails: TriagedEmail[];
  onDelete?: (msgId: string) => Promise<void>;
}

export function MailSection({ title, emoji, emails, onDelete }: Props) {
  return (
    <section className="mb-6">
      <h2 className="mb-2 text-base font-semibold text-gray-900">
        {emoji} {title} ({emails.length})
      </h2>
      {emails.length === 0 ? (
        <SectionMessage kind="empty" wrap={false}>None</SectionMessage>
      ) : (
        <div className="rounded border border-gray-200 bg-white px-4">
          {emails.map((e) => (
            <MailRow key={e.msg_id} email={e} onDelete={onDelete} />
          ))}
        </div>
      )}
    </section>
  );
}
