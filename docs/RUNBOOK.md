# Portal Runbook

Recovery guide for the six-months-from-now you. All paths absolute. All commands copy-pasteable from `C:/Users/guoyu/Projects/portal` unless noted.

## 1. `run_automation.py` exit codes

From `pipeline/scripts/run_automation.py` (constants near top, stage wiring in `main()`):

| Code | Label | What to do |
|----|----|----|
| 0 | OK (or "no changes detected") | Nothing. Silent success. |
| 1 | BUILD FAILED | `build_timemachine_db.py` crashed. Re-run with `.venv/Scripts/python.exe scripts/build_timemachine_db.py` from `pipeline/` to see the raw traceback. Usually: missing CSV, Qianji DB locked, yfinance 4xx. |
| 2 | PARITY GATE FAILED | `verify_vs_prod.py` saw drift. See §2. Sync did NOT run — prod is safe. |
| 3 | SYNC FAILED | `sync_to_d1.py` errored (wrangler auth, network, bad SQL). Re-run `python scripts/sync_to_d1.py` manually from `pipeline/` to see stdout. |
| 4 | POSITIONS GATE FAILED | `verify_positions.py` — replayed shares don't match Fidelity's `Portfolio_Positions_*.csv`. Move the stale CSV out of Downloads to re-run, or investigate replay logic. |

Logs: `%LOCALAPPDATA%/portal/logs/sync-YYYY-MM-DD.log`. Emails (if `PORTAL_SMTP_*` set) fire on any non-zero exit.

## 2. `verify_vs_prod` parity failure

Output format (from `verify_vs_prod.py::compare_row_counts`):

```
  ✗ qianji_transactions: local=1234 prod=1245 (local SHORT by 11 — DATA LOSS RISK)
```

- `local=N prod=M` means local SQLite has N rows, remote D1 has M. Gate fails only when `local < prod` for a non-DIFF table (i.e. `fidelity_transactions`, `qianji_transactions`, `computed_daily`).
- **`--expected-drops TABLE=N`** — acknowledge-only. Declares "yes I know local is short by exactly N, it's intentional (e.g. I added an ingest filter)". NOT a fix. Passes gate but doesn't change what syncs. Example: `python scripts/run_automation.py --expected-drops qianji_transactions=11`.
- **`sync_to_d1.py --full <table>`** — doesn't exist as-scoped. The real destructive escape hatch is `python scripts/sync_to_d1.py --full` (wipes ALL tables in `TABLES_TO_SYNC` and reinserts). Last resort, used e.g. PR #203's one-shot Qianji reconciliation. Run a `--dry-run` first; the `sync_log` row records the full-replace in prod for forensics.

## 3. D1 schema drift

`sync_to_d1.py::_ensure_d1_schema_aligned` auto-runs `ALTER TABLE ADD COLUMN` when local has a column D1 doesn't. Every ALTER writes one row to `sync_log` (op=`alter`).

Inspect history:

```bash
cd worker && npx wrangler d1 execute portal-db --remote \
  --command="SELECT * FROM sync_log ORDER BY id DESC LIMIT 10"
```

Auto-ALTER refuses non-TEXT `NOT NULL` columns with no `DEFAULT` (see `_column_add_ddl`). If the error says "no safe implicit default exists," either add a `DEFAULT` in `pipeline/etl/db.py`, or ALTER manually:

```bash
cd worker && npx wrangler d1 execute portal-db --remote \
  --command="ALTER TABLE <table> ADD COLUMN <col> <type> NOT NULL DEFAULT <value>"
```

Then re-run `python scripts/sync_to_d1.py` — alignment pass will see no gap and move on.

## 4. Worker 503 on `/timeline`

Live tail:

```bash
cd worker && npx wrangler tail portal-worker --format=pretty
```

Dashboard alternative: cloudflare.com → Workers & Pages → portal-worker → Logs (Real-time).

Most likely causes, in order:
- **View missing a column** — a local ingest added a field, sync ALTER-ed the base table, but the `v_*` view still projects the old list. Re-run `python pipeline/scripts/gen_schema_sql.py` and redeploy the worker. Schema drift history is in `sync_log` (see §3).
- **D1 query timeout** — `/timeline` pulls ~4.6 MB. If `v_daily` starts hanging, check D1 dashboard for row-count explosion. Rare.
- **Optional section error** — market/holdings/txns degrade to `null` + `errors: {market?…}` per section, so a 503 means the critical `v_daily` query itself failed.

## 5. Frontend broken but worker OK

Verify worker first: `curl https://portal.guoyuer.com/api/timeline | head -c 200`. If JSON, the problem is in the Pages bundle.

Check CI:

```bash
gh run list --workflow=deploy.yml --limit 5
```

Manual deploy:

```bash
MSYS_NO_PATHCONV=1 NEXT_PUBLIC_TIMELINE_URL='https://portal.guoyuer.com/api' npx next build
npx wrangler pages deploy out --project-name=portal --commit-dirty=true
```

**Do NOT omit `MSYS_NO_PATHCONV=1`** in Git Bash — MSYS rewrites the URL value into `C:/Program Files/Git/api`, bakes `file:///...` into the JS bundle, and the site silently fails at fetch time. Verify post-build: `grep -r "portal.guoyuer.com/api" out/_next/static/chunks/*.js | head -3`.

## 6. Regression baseline went stale

Symptom: CI or `pytest tests/regression/` reports a L1 hash mismatch after a legitimate behavior change (e.g. a CNY conversion fix, a new ingest filter, a rounding correction). The committed `pipeline/tests/regression/baseline/*.sha256` files need to move.

Attach the **`baseline-refresh`** label to the PR. `.github/workflows/regression-baseline-refresh.yml` rebuilds the fixture-derived DB (same inputs as the L2 `test_pipeline_golden.py`), overwrites `computed_daily.sha256` + `computed_daily_tickers.sha256` (the `.json` companions are gitignored), pushes one commit back to the PR branch, comments, and removes the label so a follow-up push does not re-trigger. Review the diff in the bot commit — if the baseline move reflects your intended behavior change, merge; if not, revert the bot commit and re-investigate.

To refresh locally instead: `cd pipeline && python scripts/refresh_l1_baseline_from_fixtures.py`.

## 7. Rebuilding from scratch (disk crash / clean laptop)

What you need on disk:

- Qianji SQLite: `%APPDATA%/com.mutangtech.qianji.win/qianji_flutter/qianjiapp.db` (reinstall Qianji Desktop + restore backup).
- Fidelity CSVs in `%USERPROFILE%/Downloads/`: `Accounts_History*.csv`, `Portfolio_Positions_*.csv`. Re-export from fidelity.com.
- Robinhood: `Robinhood_history.csv` (optional if still held).
- `pipeline/.env` with `FRED_API_KEY=...` (see MEMORY ref).
- `worker/.env.access` with `CF_ACCESS_CLIENT_ID` + `CF_ACCESS_CLIENT_SECRET` for remote-D1 dev.

Bootstrap:

```bash
git clone https://github.com/Guoyuer/portal.git && cd portal
cd pipeline && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python.exe scripts/build_timemachine_db.py
.venv/Scripts/python.exe scripts/verify_vs_prod.py   # should PASS against existing prod
.venv/Scripts/python.exe scripts/sync_to_d1.py       # diff sync catches up local to prod
```

For frontend: `cd .. && npm install && npm run build`. Pages deploy inherits the existing project via `wrangler pages deploy out --project-name=portal`.

## 8. `PORTAL_HEALTHCHECK_URL` is recommended

`run_automation.py` logs a loud WARNING at startup when `PORTAL_HEALTHCHECK_URL` is unset but continues. Automation failures then only surface via email — no external dead-man's switch.

Fix: set the var in `pipeline/.env` (or at user level via `setx`). Value format: `https://hc-ping.com/<your-uuid>`. Create the check at https://healthchecks.io/ → new check → copy the ping URL. See `docs/automation-setup.md` §1-§2 for the full walkthrough.
