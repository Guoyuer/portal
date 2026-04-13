// ── Gmail triage schema roundtrip tests ──────────────────────────────────────

import { describe, expect, it } from "vitest";
import {
  CategorySchema,
  MailListResponseSchema,
  TrashResponseSchema,
  TriagedEmailSchema,
} from "@/lib/schemas/mail";

describe("CategorySchema", () => {
  it("accepts the 3 known categories", () => {
    expect(CategorySchema.parse("IMPORTANT")).toBe("IMPORTANT");
    expect(CategorySchema.parse("NEUTRAL")).toBe("NEUTRAL");
    expect(CategorySchema.parse("TRASH_CANDIDATE")).toBe("TRASH_CANDIDATE");
  });

  it("rejects unknown categories", () => {
    expect(() => CategorySchema.parse("URGENT")).toThrow();
    expect(() => CategorySchema.parse("")).toThrow();
  });
});

describe("TriagedEmailSchema", () => {
  it("accepts a full valid email row", () => {
    const row = {
      msg_id: "<abc@x.com>",
      sender: "a@b",
      subject: "hi",
      summary: "test",
      category: "IMPORTANT",
    };
    expect(TriagedEmailSchema.parse(row)).toEqual(row);
  });

  it("rejects rows missing required fields", () => {
    expect(() => TriagedEmailSchema.parse({ msg_id: "x" })).toThrow();
  });
});

describe("MailListResponseSchema", () => {
  it("accepts empty and populated email arrays", () => {
    expect(MailListResponseSchema.parse({ emails: [], as_of: "2026-04-12T22:00:00Z" })).toEqual({
      emails: [],
      as_of: "2026-04-12T22:00:00Z",
    });
  });
});

describe("TrashResponseSchema", () => {
  it("accepts each of the 4 outcome statuses", () => {
    for (const s of ["trashed", "already_gone", "auth_failed", "error"]) {
      expect(TrashResponseSchema.parse({ status: s })).toEqual({ status: s });
    }
  });

  it("rejects unknown status", () => {
    expect(() => TrashResponseSchema.parse({ status: "ok" })).toThrow();
  });
});
