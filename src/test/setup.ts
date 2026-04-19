// Global test setup. Loaded by vitest via `test.setupFiles` — runs once per
// test file, before any test modules import.
//
// Installs minimal jsdom polyfills that many tests previously stubbed in
// per-file beforeAll hooks.

if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}
