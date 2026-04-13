import { describe, it, expect } from "vitest";
import { imapOk, parseSearchUid } from "./imap-parse";

describe("imapOk", () => {
  it("matches a bare OK tag at line start", () => {
    expect(imapOk("A1 OK LOGIN completed\r\n", "A1")).toBe(true);
  });

  it("matches OK inside a multi-line response (after untagged lines)", () => {
    const resp = "* OK Gimap ready\r\n* CAPABILITY IMAP4rev1\r\nA2 OK SELECT completed\r\n";
    expect(imapOk(resp, "A2")).toBe(true);
  });

  it("returns false for NO / BAD tag completion", () => {
    expect(imapOk("A1 NO authentication failed\r\n", "A1")).toBe(false);
    expect(imapOk("A1 BAD syntax error\r\n", "A1")).toBe(false);
  });

  it("does not match a different tag's OK", () => {
    expect(imapOk("A1 OK completed\r\n", "A2")).toBe(false);
  });
});

describe("parseSearchUid", () => {
  it("extracts the first UID from a `* SEARCH` line", () => {
    expect(parseSearchUid("* SEARCH 1423\r\nA3 OK\r\n")).toBe("1423");
  });

  it("returns the first match when SEARCH has multiple UIDs", () => {
    expect(parseSearchUid("* SEARCH 100 200 300\r\n")).toBe("100");
  });

  it("returns null when SEARCH has no results", () => {
    expect(parseSearchUid("* SEARCH\r\nA3 OK\r\n")).toBeNull();
  });

  it("returns null when the response has no SEARCH line", () => {
    expect(parseSearchUid("A3 BAD no such command\r\n")).toBeNull();
  });
});
