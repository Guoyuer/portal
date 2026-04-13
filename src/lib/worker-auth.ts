// ── Shared Worker auth helpers ───────────────────────────────────────────
// Both Cloudflare Workers (`portal-api`, `worker-gmail`) front their real
// Custom Domain with Cloudflare Access; this file owns the contract for
// inspecting the JWT that Access injects, so the two workers can't drift.
//
// Pulled in from each worker's tsconfig via a relative include, matching
// the existing pattern for `src/lib/schemas`.

export interface AuthEnv {
  // Dashboard-managed variable. When "true", the Worker is behind CF Access
  // on a Custom Domain; every request must carry a verified email header.
  // Any other value (including undefined) disables the check — used for
  // `wrangler dev` and the pre-migration `.workers.dev` URL.
  REQUIRE_AUTH?: string;
  // The single email address allowed by Access (the Worker is single-user).
  ALLOWED_EMAIL?: string;
}

/** Does the incoming request carry a CF Access JWT matching ALLOWED_EMAIL?
 *  Does **not** consult REQUIRE_AUTH — call this only when the caller has
 *  already decided an Access check is required. */
export function cfAccessEmailMatches(request: Request, env: AuthEnv): boolean {
  if (!env.ALLOWED_EMAIL) return false;
  const email = request.headers.get("Cf-Access-Authenticated-User-Email");
  return email !== null && email === env.ALLOWED_EMAIL;
}

/** Gate for the common "require CF Access unless REQUIRE_AUTH is off" case.
 *  Returns true (allow) when REQUIRE_AUTH is unset, otherwise defers to
 *  `cfAccessEmailMatches`. */
export function isAllowedUser(request: Request, env: AuthEnv): boolean {
  if (env.REQUIRE_AUTH !== "true") return true;
  return cfAccessEmailMatches(request, env);
}
