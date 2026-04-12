# Portal Automation — Setup Guide

One-time steps to register `run_portal_sync.ps1` with Windows Task Scheduler and wire up observability (healthchecks.io heartbeat + Gmail changelog emails). After this, a new Fidelity CSV dropped in `~/Downloads/` will be automatically ingested + synced to prod D1 on the next scheduled tick, and you'll get an email summarising what changed.

**Prerequisites** (assumed already in place):
- `pipeline/.venv` is set up with Python 3.14 + requirements
- `wrangler` is authenticated (`wrangler login` has been run locally)
- `pipeline/data/timemachine.db` exists and is in sync with prod D1

---

## 1. Healthchecks.io check (optional but recommended)

Gives you a heartbeat monitor — alerts if the task didn't run or failed. Free tier is enough.

1. Sign up at https://healthchecks.io/accounts/signup/
2. Create a new check:
   - **Name**: `PortalSync`
   - **Schedule**: daily, cron `0 6 * * *` (or whatever time you'll schedule)
   - **Grace**: 30 minutes
3. Copy the ping URL (format: `https://hc-ping.com/<uuid>`)

## 2. Set `PORTAL_HEALTHCHECK_URL`

Set the env var at the user level so Task Scheduler inherits it:

```cmd
setx PORTAL_HEALTHCHECK_URL "https://hc-ping.com/<your-uuid>"
```

**Note**: `setx` affects future processes only; current shells won't see it. Open a new PowerShell to verify: `echo $env:PORTAL_HEALTHCHECK_URL`.

Skip this step if you don't want monitoring — `run_automation.py` is silent when `PORTAL_HEALTHCHECK_URL` is unset.

## 3. Dry-run first (sanity check before scheduling)

From a new PowerShell window:

```powershell
cd C:\Users\guoyu\Projects\portal
powershell -NoProfile -ExecutionPolicy Bypass -File pipeline\scripts\run_portal_sync.ps1 --dry-run --force
```

**Expected**:
- Runs `[1]` → `[2]` (build) → `[3]` (verify vs prod) → `[3b]` (positions gate if a fresh `Portfolio_Positions_*.csv` is in Downloads) → `[4] Dry run — skipping sync`
- Exits `0`
- Log written to `%LOCALAPPDATA%\portal\logs\sync-YYYY-MM-DD.log`
- If healthchecks is configured, dashboard shows one successful ping

If `[3]` exits `2` (parity gate fail) or `[3b]` exits `4` (positions gate fail), investigate before proceeding — something real is off.

## 4. Register Task Scheduler

From an **elevated** PowerShell (Run as Administrator):

```powershell
schtasks /create `
  /tn "PortalSync" `
  /tr "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\guoyu\Projects\portal\pipeline\scripts\run_portal_sync.ps1" `
  /sc daily /st 06:00 `
  /rl HIGHEST
```

Flags:
- `/rl HIGHEST` — run with highest privileges (needed if the task needs to read protected paths)
- `/sc daily /st 06:00` — every day at 6 AM local time
- If you want a different cadence: `/sc weekly /d MON /st 08:00`, etc.

## 5. Trigger a test run (real sync, not dry-run)

```powershell
schtasks /run /tn "PortalSync"
```

Wait ~3 minutes, then check:

```powershell
# Last run status
schtasks /query /tn "PortalSync" /fo LIST /v | Select-String "Last Run|Last Result"

# Today's log
Get-Content "$env:LOCALAPPDATA\portal\logs\sync-$(Get-Date -Format 'yyyy-MM-dd').log" -Tail 40
```

Expected: `Last Result: 0`, log ends with `Done`.

## 6. Confirm prod got the sync

```bash
cd worker
npx wrangler d1 execute portal-db --remote --command="SELECT * FROM sync_meta"
```

Expected: `last_sync` timestamp is within the last few minutes.

## 7. Email notifications (optional but recommended)

Sends a changelog email whenever a sync detects real changes OR any gate fails. Silent no-change runs are never emailed (noise avoidance — `healthchecks.io` already tells you if the task stopped running).

Uses Gmail SMTP + an app-specific password. No DNS setup, no paid service.

1. On Gmail: **Account → Security → 2-Step Verification → App passwords**. Generate a password named `Portal Sync`. You'll get a 16-char code (shown once — save it now).

2. Set the env vars at user scope so Task Scheduler inherits them:

```cmd
setx PORTAL_SMTP_USER "your-gmail@gmail.com"
setx PORTAL_SMTP_PASSWORD "abcd efgh ijkl mnop"
```

The password can include the spaces as shown or be stripped — Gmail accepts both. Optional overrides:

```cmd
setx PORTAL_EMAIL_TO "alerts@example.com"
setx PORTAL_SMTP_HOST "smtp.gmail.com"
setx PORTAL_SMTP_PORT "587"
```

`PORTAL_EMAIL_FROM` / `PORTAL_EMAIL_TO` default to `PORTAL_SMTP_USER` (self-email).

### Alternative: `pipeline/.env` (developer convenience)

If you prefer a file-based config (no `setx`, easy to review/edit), copy
`pipeline/.env.example` to `pipeline/.env` and fill in the same keys:

```bash
cp pipeline/.env.example pipeline/.env
# then edit pipeline/.env with your Gmail + FRED key
```

The entry scripts (`run_automation.py`, `build_timemachine_db.py`,
`sync_to_d1.py`, `verify_vs_prod.py`, `verify_positions.py`) auto-load
`pipeline/.env` on startup via `etl.dotenv_loader`.

**Precedence**: environment variables set via `setx` or shell always win over
`.env` entries (`override=False`). This means Task Scheduler sees your
`setx`-persisted values first; `.env` is a fallback for interactive dev work
on a machine that hasn't had `setx` run yet. `pipeline/.env` is gitignored,
but `pipeline/.env.example` is committed as a template — see it for the
full list of supported keys.

3. Open a new PowerShell (`setx` only affects future processes) and verify:

```powershell
echo $env:PORTAL_SMTP_USER
```

4. Test with a force sync:

```powershell
cd C:\Users\guoyu\Projects\portal
powershell -NoProfile -ExecutionPolicy Bypass -File pipeline\scripts\run_portal_sync.ps1 --force
```

Check your inbox. Expected subject: `[Portal Sync] OK — N fidelity, M qianji, nw +$X`.

**Email triggers**:
- Sync detects changes (new fidelity/qianji/computed_daily/empower rows) → email with changelog
- Any gate failure (exit code 1/2/3/4) → email with error + log path
- Clean run with no changes → NO email

**Security**: the app password lives only in env vars (never in code, logs, or commits). The orchestrator logs `Email reporting: enabled` or `disabled` — never the credential itself. SMTP failures are logged but never fail the sync (exit code is preserved).

---

## Exit code reference

Exit code from `run_automation.py` (propagated by PS1 shim to Task Scheduler):

| Code | Meaning | Action |
|---|---|---|
| `0` | Success, or no changes detected | None |
| `1` | Build failed | Check log for Python stack trace in `[2]` |
| `2` | Parity gate failed — local ↔ prod drift or local shrinkage | Run `verify_vs_prod.py --verbose` manually, investigate |
| `3` | Sync failed | Check wrangler output in log; may be a transient Cloudflare issue, retry |
| `4` | Positions gate failed — replay disagrees with a fresh `Portfolio_Positions_*.csv` | Run `verify_positions.py --positions <path>` manually, investigate share-count drift |

The email notification is sent **after** the exit code is determined but before the script returns, so a failing run both emails you and exits non-zero for Task Scheduler / healthchecks to pick up.

Healthchecks.io pings:
- `/start` at each run begin
- base URL on success (including no-change)
- `/fail` on any non-zero exit

---

## Change detection patterns

The script skips unless any of these are newer than `pipeline/data/.last_run`:

- Qianji DB at `%APPDATA%\com.mutangtech.qianji.win\qianji_flutter\qianjiapp.db`
- `Accounts_History*.csv` in Downloads (Fidelity)
- `Bloomberg.Download*.qfx` in Downloads (Empower 401k)
- `Robinhood_history.csv` in Downloads
- `Portfolio_Positions_*.csv` in Downloads (Fidelity snapshot — drives the `[3b]` ground-truth gate)

When a fresh `Portfolio_Positions_*.csv` is detected, step `[3b]` runs `verify_positions.py` against it and blocks the sync if replay disagrees (exit code 4). If no such CSV is present on a given run, `[3b]` is skipped (not failed).

---

## Troubleshooting

**Task Scheduler says "Last Result: 0x1" but log is empty**  
PS1 shim failed before logging started. Usually a path issue — verify `pipeline\.venv\Scripts\python.exe` exists, or re-check the PS1 path in `schtasks /query`.

**`npx wrangler` errors in log**  
Either wrangler auth expired (`wrangler login` from the task's user account) or network. Test manually: `cd worker && npx wrangler d1 execute portal-db --remote --command="SELECT 1"`.

**Parity gate fires every day with `local SHORT by N`**  
Local DB is missing rows prod has — likely a partial/broken rebuild. Investigate:
```bash
cd pipeline && .venv/Scripts/python.exe scripts/verify_vs_prod.py --verbose --sample-size 50
```

**Change detection never fires**  
Check marker file: `pipeline\data\.last_run` — if mtime is in the future, change detection will skip everything. Delete it to force a run.
