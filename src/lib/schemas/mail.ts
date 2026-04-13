// ── Gmail triage schemas ────────────────────────────────────────────────────

import { z } from "zod";

export const CategorySchema = z.enum(["IMPORTANT", "NEUTRAL", "TRASH_CANDIDATE"]);
export type Category = z.infer<typeof CategorySchema>;

export const TriagedEmailSchema = z.object({
  msg_id: z.string(),
  sender: z.string(),
  subject: z.string(),
  summary: z.string(),
  category: CategorySchema,
});
export type TriagedEmail = z.infer<typeof TriagedEmailSchema>;

export const MailListResponseSchema = z.object({
  emails: z.array(TriagedEmailSchema),
  as_of: z.string(),
});
export type MailListResponse = z.infer<typeof MailListResponseSchema>;

export const TrashResponseSchema = z.object({
  status: z.enum(["trashed", "already_gone", "auth_failed", "error"]),
});
export type TrashResponse = z.infer<typeof TrashResponseSchema>;
