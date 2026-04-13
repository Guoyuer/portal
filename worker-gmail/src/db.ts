import type { TriagedEmail, UpsertInput } from "./types.js";

export async function upsertEmails(db: D1Database, rows: UpsertInput[]): Promise<{ inserted: number; skipped: number }> {
  let inserted = 0;
  let skipped = 0;
  // INSERT OR IGNORE preserves status='trashed' for rows the user has already acted on.
  const stmt = db.prepare(
    `INSERT OR IGNORE INTO triaged_emails
       (msg_id, received_at, classified_at, sender, subject, summary, category)
     VALUES (?, ?, ?, ?, ?, ?, ?)`
  );
  for (const r of rows) {
    const result = await stmt
      .bind(r.msg_id, r.received_at, r.classified_at, r.sender, r.subject, r.summary, r.category)
      .run();
    if (result.meta.changes === 1) inserted++;
    else skipped++;
  }
  return { inserted, skipped };
}

export async function listActiveLast7Days(db: D1Database): Promise<TriagedEmail[]> {
  const { results } = await db
    .prepare(
      `SELECT msg_id, received_at, classified_at, sender, subject, summary, category, status
         FROM triaged_emails
        WHERE status = 'active'
          AND classified_at > datetime('now', '-7 days')
        ORDER BY received_at DESC`
    )
    .all<TriagedEmail>();
  return results ?? [];
}

export async function markTrashed(db: D1Database, msgId: string): Promise<boolean> {
  const result = await db
    .prepare(`UPDATE triaged_emails SET status = 'trashed' WHERE msg_id = ? AND status = 'active'`)
    .bind(msgId)
    .run();
  return result.meta.changes > 0;
}
