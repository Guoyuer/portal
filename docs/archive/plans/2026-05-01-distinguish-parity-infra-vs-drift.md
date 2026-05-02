# Distinguish "Parity Gate Could Not Run" from "Parity Drift" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the parity gate's failure modes — "gate could not run" (auth / network / wrangler crash) vs "gate ran and rejected" (real local↔prod drift) — into separate exit codes so the email subject + Status line + Blocked-at line tell the operator at a glance whether to retry or investigate data.

**Architecture:** One new exit code (`EXIT_PARITY_INFRA = 5`). `verify_vs_prod.py` itself returns rc=1 for drift (unchanged) and rc=2 for infrastructure failures it catches around `run_wrangler_query`. `runner.py` maps verify's rc into the runner's email exit code (1 → 2, 2 → 5, anything else non-zero → 2 conservatively). Email template + tests + status-label table extend by one row each. No backcompat shims — once shipped, code 2 means drift only.

**Tech Stack:** Python 3.11, pytest, Jinja2 (existing email template).

**Context:** Triggered by the 2026-05-01 18:26 sync run that hit a transient Cloudflare 7403 inside the parity gate; the email reported `PARITY GATE FAILED` / `Blocked at: parity check (verify_vs_prod)` even though no actual drift was detected. See `pipeline/etl/automation/_constants.py:18` and `pipeline/scripts/_wrangler.py:42` for the original error path.

---

## File Map

**Constants + label:**
- Modify `pipeline/etl/automation/_constants.py` — add `EXIT_PARITY_INFRA = 5` and its `_STATUS_LABELS` entry.

**Gate script:**
- Modify `pipeline/scripts/verify_vs_prod.py` — module docstring (exit-code section), add `_INFRA_EXIT_CODE`, wrap each `run_wrangler_query` call to convert `RuntimeError` → `sys.exit(_INFRA_EXIT_CODE)`.

**Runner:**
- Modify `pipeline/etl/automation/runner.py` — import `EXIT_PARITY_INFRA`; in the verify-vs-prod stage, translate the script's rc (1 → `EXIT_PARITY_FAIL`, 2 → `EXIT_PARITY_INFRA`, other non-zero → `EXIT_PARITY_FAIL`) before calling `_report_stage_failure`; update the file's exit-code taxonomy comment block.

**Changelog gate-name table:**
- Modify `pipeline/etl/changelog/categorize.py` — add `5: "parity check (verify_vs_prod): infra error"` to `_EXIT_GATE_NAMES`.

**Subject line (small enhancement bundled here so the inbox is scannable):**
- Modify `pipeline/etl/changelog/render.py::build_subject` — append the status label after `FAIL` for non-zero exit codes.

**Docs:**
- Modify `pipeline/scripts/run_automation.py` and `pipeline/etl/automation/runner.py` exit-code docstrings — add line for code 5.

**Tests:**
- Modify `pipeline/tests/unit/test_verify_vs_prod.py` — add infra-vs-drift exit-code tests (monkeypatched `run_wrangler_query`).
- Modify `pipeline/tests/unit/test_run_automation.py` — add `test_parity_infra_fail_returns_5` and `test_parity_drift_still_returns_2`.
- Modify `pipeline/tests/unit/test_changelog.py` — add `test_format_text_header_blocked_at_on_parity_infra_failure` and `test_build_subject_includes_label_on_failure`.

---

## Task 1: Add the new exit code + status label

**Files:**
- Modify: `pipeline/etl/automation/_constants.py`

- [ ] **Step 1: Write the failing test**

Modify `pipeline/tests/unit/test_run_automation.py` near the existing `from etl.automation._constants import (...)` block — add `EXIT_PARITY_INFRA` to the import and a small standalone assertion:

```python
# At top of file, extend the existing import:
from etl.automation._constants import (
    EXIT_BUILD_FAIL,
    EXIT_OK,
    EXIT_PARITY_FAIL,
    EXIT_PARITY_INFRA,
    EXIT_POSITIONS_FAIL,
    EXIT_SYNC_FAIL,
    _STATUS_LABELS,
)


def test_parity_infra_exit_code_has_distinct_label() -> None:
    """Code 5 must be present and labelled separately from code 2 (drift)."""
    assert EXIT_PARITY_INFRA == 5
    assert _STATUS_LABELS[EXIT_PARITY_INFRA] == "PARITY GATE COULD NOT RUN"
    assert _STATUS_LABELS[EXIT_PARITY_FAIL] == "PARITY GATE FAILED"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_run_automation.py::test_parity_infra_exit_code_has_distinct_label -v`
Expected: ImportError on `EXIT_PARITY_INFRA`.

- [ ] **Step 3: Implement**

Edit `pipeline/etl/automation/_constants.py`:

```python
"""Shared exit codes + status labels — broken out to avoid runner<->notify circular import."""
from __future__ import annotations

# ── Exit codes ───────────────────────────────────────────────────────────────

EXIT_OK = 0
EXIT_BUILD_FAIL = 1
EXIT_PARITY_FAIL = 2
EXIT_SYNC_FAIL = 3
EXIT_POSITIONS_FAIL = 4
EXIT_PARITY_INFRA = 5


# ── Status labels (email subject / body rendering) ───────────────────────────

_STATUS_LABELS = {
    EXIT_OK: "OK",
    EXIT_BUILD_FAIL: "BUILD FAILED",
    EXIT_PARITY_FAIL: "PARITY GATE FAILED",
    EXIT_SYNC_FAIL: "SYNC FAILED",
    EXIT_POSITIONS_FAIL: "POSITIONS GATE FAILED",
    EXIT_PARITY_INFRA: "PARITY GATE COULD NOT RUN",
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_run_automation.py::test_parity_infra_exit_code_has_distinct_label -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/etl/automation/_constants.py pipeline/tests/unit/test_run_automation.py
git commit -m "feat(automation): add EXIT_PARITY_INFRA exit code + label"
```

---

## Task 2: Add gate-name entry for code 5

**Files:**
- Modify: `pipeline/etl/changelog/categorize.py`

- [ ] **Step 1: Write the failing test**

Append to `pipeline/tests/unit/test_changelog.py` in the same class as the other `Blocked at` tests:

```python
def test_format_text_header_blocked_at_on_parity_infra_failure(self) -> None:
    """exit_code=5 → header carries 'parity check (verify_vs_prod): infra error'."""
    body = format_text(
        SyncChangelog(),
        _ctx(exit_code=5, status_label="PARITY GATE COULD NOT RUN"),
    )
    assert "Blocked at: parity check (verify_vs_prod): infra error" in body
    assert "Status: PARITY GATE COULD NOT RUN" in body
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_changelog.py::TestFormatText::test_format_text_header_blocked_at_on_parity_infra_failure -v`
Expected: FAIL — gate label fallback `step (exit 5)` instead of the new wording.

> If your existing class name differs from `TestFormatText`, drop the class qualifier (`pytest -k test_format_text_header_blocked_at_on_parity_infra_failure`).

- [ ] **Step 3: Implement**

In `pipeline/etl/changelog/categorize.py`, extend `_EXIT_GATE_NAMES`:

```python
_EXIT_GATE_NAMES: dict[int, str] = {
    1: "build",
    2: "parity check (verify_vs_prod)",
    3: "sync",
    4: "positions check (verify_positions)",
    5: "parity check (verify_vs_prod): infra error",
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_changelog.py -k blocked_at -v`
Expected: PASS (new test + the four existing `Blocked at` tests still green).

- [ ] **Step 5: Commit**

```bash
git add pipeline/etl/changelog/categorize.py pipeline/tests/unit/test_changelog.py
git commit -m "feat(changelog): label parity-infra failures distinctly in email"
```

---

## Task 3: Make verify_vs_prod.py exit 2 on wrangler infra errors

**Files:**
- Modify: `pipeline/scripts/verify_vs_prod.py`
- Modify: `pipeline/tests/unit/test_verify_vs_prod.py`

- [ ] **Step 1: Write the failing test**

Append to `pipeline/tests/unit/test_verify_vs_prod.py`:

```python
import pytest

from scripts import verify_vs_prod


def _stub_db(tmp_path):
    """Minimal sqlite DB so the early ``_DB_PATH.exists()`` check passes."""
    import sqlite3
    db_path = tmp_path / "timemachine.db"
    conn = sqlite3.connect(str(db_path))
    for table in ("fidelity_transactions", "qianji_transactions",
                  "computed_daily", "daily_close"):
        conn.execute(f"CREATE TABLE {table} (id INTEGER)")  # noqa: S608
    conn.commit()
    conn.close()
    return db_path


def test_main_exits_2_when_wrangler_query_raises(monkeypatch, tmp_path):
    """A wrangler RuntimeError (auth/network/CLI crash) must exit with the
    INFRA code (2), NOT the drift code (1). The orchestrator translates
    these into the runner-level EXIT_PARITY_INFRA / EXIT_PARITY_FAIL."""
    monkeypatch.setenv("PORTAL_DB_PATH", str(_stub_db(tmp_path)))
    monkeypatch.setattr(
        verify_vs_prod, "run_wrangler_query",
        lambda sql: (_ for _ in ()).throw(RuntimeError("wrangler query failed (rc=1) ... 7403")),
    )
    monkeypatch.setattr(sys, "argv", ["verify_vs_prod.py"])
    with pytest.raises(SystemExit) as exc:
        verify_vs_prod.main()
    assert exc.value.code == 2


def test_main_exits_1_on_real_drift(monkeypatch, tmp_path):
    """Drift (local SHORT in a non-DIFF table) keeps the existing exit 1.

    Stubs the DB with non-empty fidelity_transactions and pretends prod has
    more rows — ``compare_row_counts`` returns ``ok=False`` and main() exits 1.
    """
    import sqlite3
    db_path = tmp_path / "timemachine.db"
    conn = sqlite3.connect(str(db_path))
    for table in ("fidelity_transactions", "qianji_transactions",
                  "computed_daily", "daily_close"):
        conn.execute(f"CREATE TABLE {table} (id INTEGER)")  # noqa: S608
    conn.execute("INSERT INTO fidelity_transactions (id) VALUES (1)")
    conn.commit()
    conn.close()
    monkeypatch.setenv("PORTAL_DB_PATH", str(db_path))

    def fake_query(sql):
        if "fidelity_transactions" in sql and "COUNT" in sql:
            return [{"n": 999}]  # prod has way more → SHORT
        return []
    monkeypatch.setattr(verify_vs_prod, "run_wrangler_query", fake_query)
    monkeypatch.setattr(sys, "argv", ["verify_vs_prod.py"])
    with pytest.raises(SystemExit) as exc:
        verify_vs_prod.main()
    assert exc.value.code == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_verify_vs_prod.py::test_main_exits_2_when_wrangler_query_raises -v`
Expected: FAIL — RuntimeError propagates uncaught (Python default exit code = 1).

- [ ] **Step 3: Implement**

In `pipeline/scripts/verify_vs_prod.py`:

(a) Add a constant near the other tolerances (after line 69 `_RECENT_WINDOW_DAYS`):

```python
# Exit codes used by the gate itself. The orchestrator (etl/automation/runner.py)
# translates these into the email-level EXIT_PARITY_FAIL / EXIT_PARITY_INFRA
# so the operator can tell "data drift" from "couldn't reach prod" at a glance.
_DRIFT_EXIT_CODE = 1
_INFRA_EXIT_CODE = 2
```

(b) Replace the existing module docstring summary line (`Exits 0 when sync is safe. Exits 1 on any real failure (STOP, investigate).`) with the split version:

```python
"""Pre-sync gate: guard against local data loss and historical value drift.

Exit codes:
    0 — pass; sync is safe to proceed
    1 — drift detected; sync would silently rewrite prod history (STOP)
    2 — infrastructure error (wrangler auth/network/CLI crash); the gate
        couldn't reach prod, so drift status is unknown. Retry once env
        is healthy. Mapped by the orchestrator to EXIT_PARITY_INFRA.
"""
```

Keep the rest of the docstring as-is.

(c) Wrap each of the three `run_wrangler_query` call sites in `main()` (lines ~294, ~313, ~325) so any `RuntimeError` from `_wrangler.py` exits with the infra code. Cleanest place is a single try/except around the three numbered sections (the script does no work that could raise RuntimeError outside those calls):

```python
def main() -> None:
    args = _parse_args()
    expected_drops = _parse_expected_drops(args.expected_drops)

    if not _DB_PATH.exists():
        print(f"Error: local DB not found: {_DB_PATH}", file=sys.stderr)
        sys.exit(_DRIFT_EXIT_CODE)

    print("=" * 60)
    print("  verify_vs_prod: local timemachine.db vs Cloudflare D1")
    print("=" * 60)
    if expected_drops:
        print(f"  Declared drops: {expected_drops}")

    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row

    all_results: list[CheckResult] = []

    try:
        # Row counts
        print("\n[1] Row counts")
        for table in _TABLES_FOR_COUNT:
            local_n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            prod_rows = run_wrangler_query(f"SELECT COUNT(*) AS n FROM {table}")  # noqa: S608 — trusted constant
            prod_n = int(prod_rows[0]["n"]) if prod_rows else 0
            r = compare_row_counts(table, local_n, prod_n, expected_drop=expected_drops.get(table, 0))
            all_results.append(r)
            marker = "✓" if r.ok else "✗"
            print(f"  {marker} {table}: {r.detail}")

        # daily_close random sample
        print(f"\n[2] daily_close sample ({args.sample_size} random rows)")
        all_pairs = conn.execute("SELECT symbol, date FROM daily_close").fetchall()
        random.seed(42)
        sampled = random.sample(list(all_pairs), min(args.sample_size, len(all_pairs)))
        local_samples = []
        for sym, d in sampled:
            row = conn.execute("SELECT symbol, date, close FROM daily_close WHERE symbol=? AND date=?",
                               (sym, d)).fetchone()
            local_samples.append(dict(row))
        if local_samples:
            conditions = " OR ".join([f"(symbol='{s['symbol']}' AND date='{s['date']}')" for s in local_samples])
            prod_samples = run_wrangler_query(f"SELECT symbol, date, close FROM daily_close WHERE {conditions}")  # noqa: S608
        else:
            prod_samples = []
        for r in compare_daily_close_samples(local_samples, prod_samples):
            all_results.append(r)
            if args.verbose or not r.ok:
                marker = "✓" if r.ok else "✗"
                print(f"  {marker} {r.detail}")

        # Recent totals
        print("\n[3] computed_daily recent 7 days")
        local_totals = [dict(r) for r in conn.execute(
            "SELECT date, total FROM computed_daily ORDER BY date DESC LIMIT 7"
        ).fetchall()]
        prod_totals = run_wrangler_query(
            "SELECT date, total FROM computed_daily ORDER BY date DESC LIMIT 7"
        )
        for r in compare_recent_totals(local_totals, prod_totals):
            all_results.append(r)
            if args.verbose or not r.ok:
                marker = "✓" if r.ok else "✗"
                print(f"  {marker} {r.detail}")
    except RuntimeError as e:
        # Infra failure — wrangler couldn't reach prod (auth, 5xx, CLI crash).
        # We DON'T know whether prod has drifted, so we can't return either
        # pass or drift. Exit 2 so the orchestrator can label this distinctly
        # in the email and the operator knows it's a retry-when-healthy
        # condition, not a data-investigation condition.
        conn.close()
        print("\n" + "=" * 60, file=sys.stderr)
        print(f"  INFRA FAIL: wrangler unreachable\n  {e}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        sys.exit(_INFRA_EXIT_CODE)

    conn.close()

    # Summary
    failed = [r for r in all_results if not r.ok]
    print("\n" + "=" * 60)
    if failed:
        print(f"  FAIL: {len(failed)} mismatches")
        for r in failed:
            print(f"    - {r.table}: {r.detail}")
        print("=" * 60)
        sys.exit(_DRIFT_EXIT_CODE)
    print(f"  PASS: {len(all_results)} checks, all within tolerance")
    print("=" * 60)
```

Note: `_DRIFT_EXIT_CODE` replaces the bare `1` in both the missing-DB branch and the drift summary so the script has one named constant for "drift / pre-sync stop".

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_verify_vs_prod.py -v`
Expected: all green (new + existing).

- [ ] **Step 5: Commit**

```bash
git add pipeline/scripts/verify_vs_prod.py pipeline/tests/unit/test_verify_vs_prod.py
git commit -m "feat(verify_vs_prod): exit 2 on wrangler infra error, 1 on drift"
```

---

## Task 4: Translate verify's rc in the orchestrator

**Files:**
- Modify: `pipeline/etl/automation/runner.py`
- Modify: `pipeline/scripts/run_automation.py` (docstring only)
- Modify: `pipeline/tests/unit/test_run_automation.py`

- [ ] **Step 1: Write the failing test**

Add to the existing `TestExitCodeMapping` class in `pipeline/tests/unit/test_run_automation.py` (sits next to `test_parity_fail_returns_2`):

```python
def test_parity_infra_fail_returns_5(self, monkeypatch, tmp_path):
    """verify_vs_prod rc=2 (infra) must surface as runner EXIT_PARITY_INFRA=5,
    NOT as EXIT_PARITY_FAIL=2 — that's the whole point of this split."""
    rc, fake = self._invoke(["--force"], [0, 2], monkeypatch, tmp_path)
    assert rc == EXIT_PARITY_INFRA
    # Sync must NOT have been attempted.
    assert [c[0].name for c in fake.calls] == [
        "build_timemachine_db.py", "verify_vs_prod.py",
    ]


def test_parity_drift_still_returns_2(self, monkeypatch, tmp_path):
    """Regression: verify_vs_prod rc=1 (drift) keeps the existing
    EXIT_PARITY_FAIL=2 mapping (don't accidentally remap drift to infra)."""
    rc, _ = self._invoke(["--force"], [0, 1], monkeypatch, tmp_path)
    assert rc == EXIT_PARITY_FAIL


def test_parity_unknown_rc_treated_as_drift(self, monkeypatch, tmp_path):
    """Defensive: any other non-zero rc from verify_vs_prod (e.g. a Python
    crash that bypasses our try/except) is mapped to EXIT_PARITY_FAIL — we
    err on 'block sync' rather than 'silent retry' for unknown failure modes."""
    rc, _ = self._invoke(["--force"], [0, 99], monkeypatch, tmp_path)
    assert rc == EXIT_PARITY_FAIL
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_run_automation.py::TestExitCodeMapping -v`
Expected: the three new tests fail (rc=2 currently maps to `EXIT_PARITY_FAIL` regardless of underlying rc).

- [ ] **Step 3: Implement**

In `pipeline/etl/automation/runner.py`:

(a) Extend the import block (around lines 30–36):

```python
from ._constants import (
    EXIT_BUILD_FAIL,
    EXIT_OK,
    EXIT_PARITY_FAIL,
    EXIT_PARITY_INFRA,
    EXIT_POSITIONS_FAIL,
    EXIT_SYNC_FAIL,
)
```

(b) Replace the verify-vs-prod stage block in `Runner.run()` (currently lines 241–251):

```python
        # [3] Pre-sync gate: guard against local data loss + historical drift.
        # verify_vs_prod splits its own exit codes — 1=drift, 2=infra (wrangler
        # auth/network/CLI crash). Anything else non-zero is treated as drift
        # (block the sync conservatively).
        if not self.args.local:
            log.info("[3] Verifying historical immutability + no local data loss vs prod D1...")
            gate_args: list[str] = []
            for spec in self.args.expected_drops:
                gate_args.extend(["--expected-drops", spec])
            rc = run_python_script(SCRIPTS_DIR / "verify_vs_prod.py", *gate_args)
            if rc != 0:
                runner_exit = EXIT_PARITY_INFRA if rc == 2 else EXIT_PARITY_FAIL
                stage_label = (
                    "PRE-SYNC GATE (INFRA)" if runner_exit == EXIT_PARITY_INFRA
                    else "PRE-SYNC GATE"
                )
                return self._report_stage_failure(
                    log, stage_label, rc, runner_exit, "verify_vs_prod.py",
                    email_config, snapshot_before, db_path, log_file,
                )
```

(c) Update the file-level exit-code taxonomy comment block (lines 10–15) and the `run_automation.py` docstring (lines 11–17) to add the new code:

```python
"""...
Exit-code taxonomy (constants live in :mod:`etl.automation._constants`):
    0 — ok, or no changes detected (both normal outcomes for cron)
    1 — build failed
    2 — verify_vs_prod found drift (local <-> prod parity drift — do NOT sync)
    3 — sync failed
    4 — verify_positions failed (replay disagrees with Fidelity snapshot)
    5 — verify_vs_prod could not run (wrangler auth/network/CLI crash —
        retry when env is healthy; drift status unknown)
"""
```

Apply the same three-line addition to `pipeline/scripts/run_automation.py`'s module docstring (lines 11–16).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_run_automation.py::TestExitCodeMapping -v`
Expected: all green — new infra mapping works, drift mapping unchanged.

- [ ] **Step 5: Commit**

```bash
git add pipeline/etl/automation/runner.py pipeline/scripts/run_automation.py pipeline/tests/unit/test_run_automation.py
git commit -m "feat(automation): map verify_vs_prod rc=2 to EXIT_PARITY_INFRA"
```

---

## Task 5: Make the email subject scannable

**Files:**
- Modify: `pipeline/etl/changelog/render.py`
- Modify: `pipeline/tests/unit/test_changelog.py`

Subject currently reads `[Portal Sync] FAIL (exit N)` for every failure mode — you have to open the email to see whether it was build, parity, sync, or positions. Append the status label so the inbox row is self-explanatory.

- [ ] **Step 1: Write the failing test**

Add to `pipeline/tests/unit/test_changelog.py` (find the existing `TestBuildSubject` class or wherever `build_subject` is exercised — search for `build_subject(`):

```python
def test_build_subject_includes_label_on_failure(self) -> None:
    """Failure subject should name the gate, not just the exit code."""
    from etl.changelog import build_subject

    cl = SyncChangelog()
    assert build_subject(cl, 0) == "[Portal Sync] OK"
    assert build_subject(cl, 1) == "[Portal Sync] FAIL — BUILD FAILED"
    assert build_subject(cl, 2) == "[Portal Sync] FAIL — PARITY GATE FAILED"
    assert build_subject(cl, 5) == "[Portal Sync] FAIL — PARITY GATE COULD NOT RUN"
    # Unknown code falls back to the exit number.
    assert build_subject(cl, 99) == "[Portal Sync] FAIL (exit 99)"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_changelog.py -k build_subject -v`
Expected: FAIL — current subject is `[Portal Sync] FAIL (exit 1)` etc.

- [ ] **Step 3: Implement**

In `pipeline/etl/changelog/render.py`, add the import and rewrite `build_subject`:

```python
from etl.automation._constants import _STATUS_LABELS  # add to imports near top


def build_subject(changelog: SyncChangelog, exit_code: int) -> str:
    """Short, informative subject line.

    Successful syncs with changes → summary of counts. Failures → ``FAIL —
    <label>`` so the operator can triage from the inbox row alone, with an
    ``(exit N)`` fallback for unknown codes.
    """
    if exit_code != 0:
        label = _STATUS_LABELS.get(exit_code)
        if label is None:
            return f"[Portal Sync] FAIL (exit {exit_code})"
        return f"[Portal Sync] FAIL — {label}"
    bits: list[str] = []
    if changelog.fidelity_added:
        bits.append(f"{len(changelog.fidelity_added)} fidelity")
    if changelog.qianji_added_count > 0:
        bits.append(f"{changelog.qianji_added_count} qianji")
    if changelog.empower_added > 0:
        bits.append(f"{changelog.empower_added} empower")
    if changelog.net_worth_delta is not None:
        bits.append(f"nw {_fmt_delta(changelog.net_worth_delta)}")
    if not bits:
        return "[Portal Sync] OK"
    return "[Portal Sync] OK — " + ", ".join(bits)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_changelog.py -v`
Expected: all green.

> If you discover any older test asserting the literal `"FAIL (exit N)"` subject for codes 1–4, update those assertions to the new `FAIL — <LABEL>` form. There must be no half-migrated state.

- [ ] **Step 5: Commit**

```bash
git add pipeline/etl/changelog/render.py pipeline/tests/unit/test_changelog.py
git commit -m "feat(changelog): include gate label in failure email subject"
```

---

## Task 6: End-to-end smoke check

- [ ] **Step 1: Full pytest run**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest -q`
Expected: 665+ tests pass; no regressions.

- [ ] **Step 2: mypy**

Run: `cd pipeline && .venv/Scripts/mypy etl/ scripts/ --strict --ignore-missing-imports`
Expected: clean (the new constant + import additions shouldn't introduce new errors).

- [ ] **Step 3: ruff**

Run: `cd pipeline && .venv/Scripts/ruff check .`
Expected: clean.

- [ ] **Step 4: Manual smoke — simulate the original failure**

```bash
cd pipeline
# Temporarily break wrangler auth so verify_vs_prod hits the RuntimeError path.
$env:CLOUDFLARE_API_TOKEN="bogus"  # PowerShell
.venv/Scripts/python.exe scripts/verify_vs_prod.py
echo $LASTEXITCODE   # expect 2
```

Then with auth restored, run the orchestrator dry-run:

```bash
.venv/Scripts/python.exe scripts/run_automation.py --force --dry-run
echo $LASTEXITCODE   # expect 0
```

If the email config is set, the failure simulation will send a test email — confirm the subject says `FAIL — PARITY GATE COULD NOT RUN` and the body says `Blocked at: parity check (verify_vs_prod): infra error`.

- [ ] **Step 5: PR**

Push and open a PR titled `Distinguish parity-gate infra failures from drift`. Body: link this plan, paste the new email subject + body excerpt as evidence. Expect a single reviewer pass — no schema changes, no D1 changes, no frontend touch.

```bash
git push -u origin <branch>
gh pr create --title "Distinguish parity-gate infra failures from drift" --body "$(cat <<'EOF'
## Summary
- Split parity-gate failures: `EXIT_PARITY_FAIL=2` (drift) vs new `EXIT_PARITY_INFRA=5` (wrangler auth/network/CLI crash).
- `verify_vs_prod.py` exits 1 on drift, 2 on RuntimeError from `_wrangler.py`; orchestrator translates 2→5.
- Email subject now reads `FAIL — <LABEL>` so the inbox row is self-explanatory.
- Plan: `docs/plans/2026-05-01-distinguish-parity-infra-vs-drift.md`.

## Test plan
- [x] `pytest -q` clean
- [x] `mypy --strict` clean
- [x] Manual: bogus CLOUDFLARE_API_TOKEN → verify_vs_prod exits 2; orchestrator emits `FAIL — PARITY GATE COULD NOT RUN`.
- [x] Manual: real auth → dry-run sync passes (exit 0).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Checklist

- **Spec coverage:** every email surface that mentions parity (status label, Blocked-at gate name, subject) gets a code-5 row. ✓
- **No placeholders:** every step has actual code or actual command. ✓
- **Type / name consistency:** `EXIT_PARITY_INFRA = 5` is the single name used everywhere; `_STATUS_LABELS[EXIT_PARITY_INFRA] = "PARITY GATE COULD NOT RUN"`; `_EXIT_GATE_NAMES[5] = "parity check (verify_vs_prod): infra error"`. The two strings differ deliberately — `_STATUS_LABELS` is shouted in the `Status:` line, `_EXIT_GATE_NAMES` is the prose `Blocked at:` clause. Subject reuses `_STATUS_LABELS`. ✓
- **No backcompat shim:** Task 5 explicitly calls out updating any older subject-line assertion to the new format — no half-migrated state. ✓
- **Scope discipline:** plan does NOT touch the underlying OAuth refresh problem (transient, resolved itself). It only fixes the *labelling* of future infra failures so they're triageable from the inbox.
