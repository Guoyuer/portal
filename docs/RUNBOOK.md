# Runbook

## Exit Codes

| Code | Status | Meaning |
| --- | --- | --- |
| 0 | OK | No changes or successful publish |
| 1 | BUILD FAILED | `build_timemachine_db.py` failed |
| 2 | ARTIFACT VERIFY FAILED | R2 artifact export/verify failed; publish did not run |
| 3 | R2 PUBLISH FAILED | Upload/readback/manifest publish failed |
| 4 | POSITIONS GATE FAILED | `verify_positions.py` disagreed with a new Fidelity positions CSV |

## Normal Manual Publish

```bash
cd pipeline
.venv/Scripts/python.exe scripts/build_timemachine_db.py
.venv/Scripts/python.exe scripts/r2_artifacts.py export
.venv/Scripts/python.exe scripts/r2_artifacts.py verify
.venv/Scripts/python.exe scripts/r2_artifacts.py publish --remote
```

`publish` verifies again before uploading. It refuses to overwrite existing snapshot objects and flips `manifest.json` only after readback succeeds.

## Local Worker Test

```bash
bash pipeline/scripts/seed_local_r2_from_fixtures.sh
cd worker
npx wrangler dev --local --port 8787
```

Then hit:

```bash
curl http://localhost:8787/api/timeline
curl http://localhost:8787/api/econ
curl http://localhost:8787/api/prices
```

For real local data, replace the fixture seed with `r2_artifacts.py publish --local` after a local DB build.

## Artifact Verification Failure

Run the verifier directly for the exact failing section:

```bash
cd pipeline
.venv/Scripts/python.exe scripts/r2_artifacts.py verify
```

Common causes:

- SQLite row count does not match exported JSON length.
- `manifest.json` descriptor hash or byte count is stale.
- Frontend Zod schema rejects a payload.
- `computed_daily` has no latest date.
- A path-unsafe price symbol was introduced.

Fix the source data or exporter, rebuild, export, verify, and publish again.

## R2 Publish Failure

Remote publish can fail during upload, readback, or manifest flip. If snapshot objects uploaded but `manifest.json` did not flip, production still serves the old manifest. Re-run `r2_artifacts.py publish --remote` after fixing the underlying issue.

If `manifest.json` flipped to a bad version, roll back by putting a previous known-good manifest back to `manifest.json`. Snapshot objects are versioned and retained for rollback.

## Worker Failure

`/api/*` returns 5xx when:

- `PORTAL_DATA` binding is missing
- `manifest.json` is missing or invalid
- the manifest references a missing R2 object
- R2 returns an object-read failure

Check the Worker deployment and R2 bucket:

```bash
cd worker
npx wrangler deploy --dry-run
npx wrangler r2 object get portal-data/manifest.json --remote --file=manifest.remote.json
```

Do not mask these failures with fallback JSON; missing data should be explicit.

## Automation

The Task Scheduler shim calls:

```bash
cd pipeline
.venv/Scripts/python.exe scripts/run_automation.py
```

Useful flags:

- `--dry-run` - build, export, verify, skip publish
- `--force` - bypass change detection
- `--local` - publish to Wrangler local R2

The marker file is updated only after a non-dry-run publish succeeds.

## Regression Baselines

Fixture regression tests are offline:

```bash
cd pipeline
.venv/Scripts/python.exe -m pytest tests/regression/ -v
```

To intentionally refresh L1 hashes:

```bash
cd pipeline
.venv/Scripts/python.exe scripts/refresh_l1_baseline_from_fixtures.py
```

Attach the `baseline-refresh` label to a PR to run the CI refresh workflow.

## Access Headers

`worker/.env.access` is gitignored and may contain Cloudflare Access service-token credentials for remote endpoint smoke checks. Do not print it in logs. For normal local R2 testing it is not needed.
