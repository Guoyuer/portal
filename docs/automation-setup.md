# Automation Setup

This registers the Windows Task Scheduler job that runs the Portal pipeline after logon and publishes validated R2 artifacts.

## Prerequisites

- `pipeline/data/timemachine.db` can be built locally.
- Cloudflare R2 bucket `portal-data` exists.
- `worker/wrangler.toml` has `PORTAL_DATA` bound to `portal-data`.
- Wrangler is logged in for the Windows user that owns the scheduled task.
- Optional: `PORTAL_HEALTHCHECK_URL`, `PORTAL_SMTP_USER`, and `PORTAL_SMTP_PASSWORD` are configured.

Pipeline scripts auto-load `pipeline/.env`; user-level environment variables still take precedence.

## Register Task

Run in PowerShell:

```powershell
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\Users\guoyu\Projects\portal\pipeline\scripts\run_portal_sync.ps1'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = 'PT2M'
Register-ScheduledTask -TaskName 'PortalSync' -Action $action -Trigger $trigger
```

The delay gives Wi-Fi/VPN/Cloudflare auth time to settle after laptop wake or logon.

## Manual Dry Run

```powershell
cd C:\Users\guoyu\Projects\portal\pipeline
.venv\Scripts\python.exe scripts\run_automation.py --dry-run --force
```

Expected flow:

```text
change detection -> build -> optional positions gate -> R2 export -> R2 verify -> dry-run skip publish
```

## Manual Publish

```powershell
cd C:\Users\guoyu\Projects\portal\pipeline
.venv\Scripts\python.exe scripts\run_automation.py --force
```

The publish step runs `r2_artifacts.py publish --remote`, which verifies locally, uploads snapshot objects, readback-checks hashes, then flips `manifest.json`.

## Local R2 Test

```powershell
cd C:\Users\guoyu\Projects\portal\pipeline
.venv\Scripts\python.exe scripts\build_timemachine_db.py
.venv\Scripts\python.exe scripts\r2_artifacts.py export
.venv\Scripts\python.exe scripts\r2_artifacts.py publish --local
cd ..\worker
npx wrangler dev --local --port 8787
```

Use this before touching production when changing the Worker or artifact shape.

## Exit Codes

| Code | Meaning | Action |
| --- | --- | --- |
| 0 | OK / no changes | None |
| 1 | Build failed | Run `build_timemachine_db.py` manually |
| 2 | Artifact verification failed | Run `r2_artifacts.py verify` and fix exporter/data/schema |
| 3 | R2 publish failed | Check Wrangler/R2 auth/network and rerun publish |
| 4 | Positions gate failed | Compare replayed shares with the new Fidelity positions CSV |

## Healthchecks And Email

Set `PORTAL_HEALTHCHECK_URL` to a healthchecks.io ping URL if you want external failure detection. Without it, the runner logs a warning but still runs.

Set these for Gmail publish-receipt/failure email:

```text
PORTAL_SMTP_USER=
PORTAL_SMTP_PASSWORD=
PORTAL_EMAIL_TO=
```

Use a Gmail app password, not the account password.

## Troubleshooting

- `R2 snapshot object already exists`: export used an already-published version. Re-export with a new timestamp/version.
- `R2 manifest missing`: publish has not completed or Worker is pointed at the wrong bucket.
- `PORTAL_DATA R2 binding is missing`: deploy the Worker with the current `wrangler.toml`.
- Zod validation failed: regenerate schemas if `etl/types.py` changed, or fix exporter shape drift.
