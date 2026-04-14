import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

describe("config URL derivation", () => {
  const origEnv = process.env;

  beforeEach(() => {
    vi.resetModules();
    process.env = { ...origEnv };
  });

  afterEach(() => {
    process.env = origEnv;
  });

  it("derives Worker URLs from NEXT_PUBLIC_TIMELINE_URL base", async () => {
    process.env.NEXT_PUBLIC_TIMELINE_URL = "https://portal.guoyuer.com/api";
    const config = await import("./config");
    expect(config.TIMELINE_URL).toBe("https://portal.guoyuer.com/api/timeline");
    expect(config.ECON_URL).toBe("https://portal.guoyuer.com/api/econ");
  });

  it("falls back to localhost when env not set", async () => {
    delete process.env.NEXT_PUBLIC_TIMELINE_URL;
    const config = await import("./config");
    expect(config.TIMELINE_URL).toBe("http://localhost:8787/timeline");
    expect(config.ECON_URL).toBe("http://localhost:8787/econ");
  });
});
