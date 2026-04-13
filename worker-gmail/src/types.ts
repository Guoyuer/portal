export type Category = "IMPORTANT" | "NEUTRAL" | "TRASH_CANDIDATE";

export interface UpsertInput {
  msg_id: string;
  received_at: string;
  classified_at: string;
  sender: string;
  subject: string;
  summary: string;
  category: Category;
}

export interface TriagedEmail extends UpsertInput {
  status: "active" | "trashed";
}
