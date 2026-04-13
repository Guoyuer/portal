// Pure string helpers for the minimal IMAP client. Extracted so they can be
// unit-tested in Node without pulling in `cloudflare:sockets`, which the main
// index.ts imports at the top level.

export function imapOk(response: string, tag: string): boolean {
  return new RegExp(`^${tag} OK`, "m").test(response);
}

export function parseSearchUid(response: string): string | null {
  const m = response.match(/^\* SEARCH\s+(\d+)/m);
  return m ? m[1] : null;
}
