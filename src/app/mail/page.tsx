"use client";

// ── /mail page ───────────────────────────────────────────────────────────────

import { groupByCategory, useMail } from "@/lib/hooks/use-mail";
import { MailSection } from "@/components/mail/mail-list";
import { ErrorBoundary, SectionError } from "@/components/error-boundary";

export default function MailPage() {
  const { loading, error, data, deleteEmail } = useMail();

  if (loading) return <main className="p-6"><p>Loading mail…</p></main>;
  if (error) {
    return (
      <div className="max-w-5xl mx-auto py-20 text-center">
        <p className="text-red-500 mb-2">Failed to load data</p>
        <p className="text-sm text-muted-foreground">{error}</p>
      </div>
    );
  }
  if (!data) return null;

  const { important, neutral, trash } = groupByCategory(data.emails);
  const asOf = new Date(data.as_of).toLocaleString();

  return (
    <main className="mx-auto max-w-3xl p-6">
      <h1 className="mb-2 text-2xl font-bold">Mail</h1>
      <p className="mb-6 text-sm text-gray-500">as of {asOf}</p>
      <ErrorBoundary fallback={<SectionError label="Important" />}>
        <MailSection title="IMPORTANT" emoji="📌" emails={important} />
      </ErrorBoundary>
      <ErrorBoundary fallback={<SectionError label="Other" />}>
        <MailSection title="OTHER" emoji="📨" emails={neutral} />
      </ErrorBoundary>
      <ErrorBoundary fallback={<SectionError label="Suggested Trash" />}>
        <MailSection title="SUGGESTED TRASH" emoji="🗑️" emails={trash} onDelete={deleteEmail} />
      </ErrorBoundary>
    </main>
  );
}
