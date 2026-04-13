# Worker Public-URL Backdoor — Security Follow-up

> Discovered 2026-04-12 while deploying Gmail triage. Separate PR from the triage feature.

## Problem

Both Cloudflare Workers are exposed on their default `*.workers.dev` subdomains with no Cloudflare Access in front:

| Worker | URL | Auth | Exposes |
|---|---|---|---|
| `portal-api` (Finance) | `https://portal-api.guoyuer1.workers.dev/timeline` | **None** | ~5 MB JSON: per-day net-worth, per-ticker holdings, transaction log, market context, Empower 401k snapshots |
| `worker-gmail` (Mail) | `https://worker-gmail.guoyuer1.workers.dev/mail/*` | URL key (32-char random) | Classifications for last 7 days of unread Gmail: sender, subject, AI summary, category |

The `.workers.dev` subdomain is enabled by default when you deploy a Worker. It is independent of any Custom Domain + CF Access configuration. Even if you add Custom Domains later and put CF Access in front of them, the `.workers.dev` URL keeps serving the same Worker with zero auth.

Verified via direct curl:
```
curl https://portal-api.guoyuer1.workers.dev/timeline
  → HTTP 200, 5.1 MB JSON body (full finance snapshot)
```

`portal.guoyuer.com` itself **is** behind CF Access (Google SSO). The misconception is that this protects the backend — it does not. It only protects the Next.js static frontend at `portal.guoyuer.com/*`.

## Severity

**Finance: medium-high.** Anyone who learns the URL (e.g., from a leaked browser history, a pushed dev env file, or a lucky guess — `portal-api` is an obvious naming pattern) reads the user's complete financial history.

**Mail: low-medium.** URL-key auth means an attacker also needs a 32-char random secret. Realistic threat is the user accidentally leaking the URL along with localStorage dump / browser sync / devtools screenshot.

## Why this was missed

- CF Access was configured on the Pages project (`portal.guoyuer.com`) with a natural assumption it covers the whole stack.
- Deploy scripts and `NEXT_PUBLIC_TIMELINE_URL` point the frontend at `.workers.dev`, not a Custom Domain.
- Cloudflare's UI shows "Domains & Routes" as a list — it's easy to read "has a workers.dev row" as "is configured" without noticing the implication that this row is PUBLIC and unauth.

## Fix plan

### Option A (recommended): Custom Domain + CF Access on both Workers

1. **Pick a subdomain.** Suggestion: `api.guoyuer.com` or `api.portal.guoyuer.com`. DNS record: CNAME → `portal-api.guoyuer1.workers.dev` (managed by Cloudflare, so one-click).

2. **Add Custom Domains** in Cloudflare dashboard:
   - `portal-api` → Add Custom Domain → `api.guoyuer.com` (root) or path `api.guoyuer.com/timeline`
   - `worker-gmail` → Add Custom Domain → `api.guoyuer.com/mail/*` (same zone, different path)

3. **Create a CF Access application** for `api.guoyuer.com` with the same Google SSO policy already used for `portal.guoyuer.com`. Session length: 30 days (configurable).

4. **Update Worker code** to trust the CF Access JWT:
   - `portal-api`: read `Cf-Access-Authenticated-User-Email` header, allow if matches `guoyuer1@gmail.com`, else 401. CF Access verifies the JWT before the request reaches the Worker, so trusting the header is safe — but only for requests arriving via the Custom Domain (the `.workers.dev` URL would skip Access entirely, see step 6).
   - `worker-gmail`: same, then remove `USER_KEY` / `authUser()` / URL-key handling from frontend and backend.

5. **Update frontend env vars** (both `NEXT_PUBLIC_TIMELINE_URL` and `NEXT_PUBLIC_GMAIL_WORKER_URL`) in the deploy workflow and `.env.local` to point at the Custom Domain. Browser includes the CF Access cookie automatically for same-zone requests.

6. **Disable `.workers.dev` subdomain** on both Workers:
   - Cloudflare Dashboard → Worker → Settings → Domains & Routes → Workers.dev toggle → off.
   - Also disable "Preview URLs" (same page).
   - **Verify**: `curl https://portal-api.guoyuer1.workers.dev/timeline` should return 404 or 522 after the toggle propagates.

7. **Rotate the Mail USER_KEY** (optional cleanup) since the auth path is now gone: `wrangler secret delete USER_KEY` for `worker-gmail`, drop the localStorage key on the client.

### Option B (quick but partial): API token on Workers

Add a single shared secret check at the top of each Worker's fetch handler. Doesn't require CF Access, but manual token management on every device. Inferior to Option A.

### Option C (do nothing)

Accept URL-obscurity as the security model. Only viable if the data is truly non-sensitive — not the case for Finance.

## Deferred items

- GH Secrets `NEXT_PUBLIC_TIMELINE_URL` and `PORTAL_GMAIL_WORKER_URL` will need updating to the Custom Domain URL when Option A lands. Old values (`.workers.dev`) become dead links once the toggle is flipped.
- Python cron (`triage.py` on GitHub Actions) hits `PORTAL_GMAIL_WORKER_URL/mail/sync` with `X-Sync-Secret`. That secret path can stay as-is (server-to-server doesn't benefit from CF Access) OR also migrate to JWT service tokens if desired. Probably keep `SYNC_SECRET`.

## Related code pointers

- `ci.yml` build step: env vars for Pages build
- `.github/workflows/gmail-sync.yml`: Python → Worker sync endpoint
- `worker/src/index.ts`: where the Finance auth check will be added
- `worker-gmail/src/index.ts`: where `authUser()` currently is; replace with `authCfAccess(request, env)`
- `src/lib/use-mail.ts`: `resolveKey()` and localStorage logic to delete
- `src/app/mail/page.tsx`: `keyMissing` branch to delete
