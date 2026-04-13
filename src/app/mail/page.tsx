"use client";

// ── /mail page ───────────────────────────────────────────────────────────────

import { groupByCategory, useMail } from "@/lib/use-mail";
import { MailSection } from "@/components/mail/mail-list";

export default function MailPage() {
  const { loading, error, data, deleteEmail } = useMail();

  if (loading) return <main className="p-6"><p>Loading mail…</p></main>;
  if (error) return <main className="p-6"><p className="text-red-700">Error: {error}</p></main>;
  if (!data) return null;

  const { important, neutral, trash } = groupByCategory(data.emails);
  const asOf = new Date(data.as_of).toLocaleString();

  return (
    <main className="mx-auto max-w-3xl p-6">
      <h1 className="mb-2 text-2xl font-bold">Mail</h1>
      <p className="mb-6 text-sm text-gray-500">as of {asOf}</p>
      <MailSection title="IMPORTANT" emoji="📌" emails={important} />
      <MailSection title="OTHER" emoji="📨" emails={neutral} />
      <MailSection title="SUGGESTED TRASH" emoji="🗑️" emails={trash} onDelete={deleteEmail} />
    </main>
  );
}
