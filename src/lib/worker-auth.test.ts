import { describe, it, expect } from "vitest";
import { cfAccessEmailMatches, isAllowedUser } from "./worker-auth";

const req = (headers: Record<string, string> = {}) => new Request("http://x/", { headers });

// ── cfAccessEmailMatches ────────────────────────────────────────────────

describe("cfAccessEmailMatches", () => {
  it("rejects when ALLOWED_EMAIL is unset (fail-closed)", () => {
    const r = req({ "Cf-Access-Authenticated-User-Email": "me@example.com" });
    expect(cfAccessEmailMatches(r, {})).toBe(false);
  });

  it("rejects when the header is absent", () => {
    expect(cfAccessEmailMatches(req(), { ALLOWED_EMAIL: "me@example.com" })).toBe(false);
  });

  it("rejects when the header email does not match", () => {
    const r = req({ "Cf-Access-Authenticated-User-Email": "attacker@evil.com" });
    expect(cfAccessEmailMatches(r, { ALLOWED_EMAIL: "me@example.com" })).toBe(false);
  });

  it("accepts an exact match", () => {
    const r = req({ "Cf-Access-Authenticated-User-Email": "me@example.com" });
    expect(cfAccessEmailMatches(r, { ALLOWED_EMAIL: "me@example.com" })).toBe(true);
  });
});

// ── isAllowedUser ───────────────────────────────────────────────────────

describe("isAllowedUser", () => {
  it("bypasses the check when REQUIRE_AUTH is unset (dev / pre-migration)", () => {
    expect(isAllowedUser(req(), {})).toBe(true);
  });

  it("bypasses the check when REQUIRE_AUTH is any value other than 'true'", () => {
    expect(isAllowedUser(req(), { REQUIRE_AUTH: "false" })).toBe(true);
    expect(isAllowedUser(req(), { REQUIRE_AUTH: "1" })).toBe(true);
  });

  it("rejects when REQUIRE_AUTH=true but the CF Access header is absent", () => {
    expect(isAllowedUser(req(), { REQUIRE_AUTH: "true", ALLOWED_EMAIL: "me@example.com" })).toBe(false);
  });

  it("rejects when the header email does not match ALLOWED_EMAIL", () => {
    const r = req({ "Cf-Access-Authenticated-User-Email": "attacker@evil.com" });
    expect(isAllowedUser(r, { REQUIRE_AUTH: "true", ALLOWED_EMAIL: "me@example.com" })).toBe(false);
  });

  it("accepts the header email when it matches ALLOWED_EMAIL", () => {
    const r = req({ "Cf-Access-Authenticated-User-Email": "me@example.com" });
    expect(isAllowedUser(r, { REQUIRE_AUTH: "true", ALLOWED_EMAIL: "me@example.com" })).toBe(true);
  });

  it("rejects when ALLOWED_EMAIL is unset (fail-closed)", () => {
    const r = req({ "Cf-Access-Authenticated-User-Email": "me@example.com" });
    expect(isAllowedUser(r, { REQUIRE_AUTH: "true" })).toBe(false);
  });
});
