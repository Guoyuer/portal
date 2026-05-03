"""Tests for scripts/run_automation.py — the Python orchestrator that replaces
the old PowerShell logic. Covers change detection, CLI parsing, exit-code
mapping, and healthcheck behaviour.

After the ``etl/automation/`` split (PR refactor/a2-automation-split) the
orchestration logic moved into the package; this file now patches canonical
symbols on those submodules (``etl.automation.runner`` etc.) rather than the
old monolithic ``scripts/run_automation`` module. The entry-point script
itself stays as a thin ``parse_args → Runner.from_args → run`` shim.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from etl.automation import (  # noqa: E402
    EXIT_BUILD_FAIL,
    EXIT_OK,
    EXIT_PARITY_FAIL,
    EXIT_POSITIONS_FAIL,
    EXIT_SYNC_FAIL,
    changes,
    notify,
    paths,
    runner,
)
from etl.automation._constants import _STATUS_LABELS  # noqa: E402
from scripts import run_automation  # noqa: E402


def test_artifact_verify_exit_code_label() -> None:
    assert _STATUS_LABELS[EXIT_PARITY_FAIL] == "ARTIFACT VERIFY FAILED"
    assert _STATUS_LABELS[EXIT_SYNC_FAIL] == "R2 PUBLISH FAILED"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def downloads(tmp_path: Path) -> Path:
    d = tmp_path / "Downloads"
    d.mkdir()
    return d


@pytest.fixture()
def marker(tmp_path: Path) -> Path:
    return tmp_path / ".last_run"


@pytest.fixture()
def qianji_db_file(tmp_path: Path) -> Path:
    p = tmp_path / "qianjiapp.db"
    return p


@pytest.fixture(autouse=True)
def _silence_logs(caplog):
    """Let pytest-caplog capture but don't let real handlers spam stdout during tests."""
    # Clear handlers attached by setup_logging() in prior tests.
    for h in list(logging.getLogger().handlers):
        logging.getLogger().removeHandler(h)
        h.close()
    caplog.set_level(logging.INFO)
    yield
    for h in list(logging.getLogger().handlers):
        logging.getLogger().removeHandler(h)
        h.close()


# ── changes_detected() ────────────────────────────────────────────────────────

class TestChangesDetected:
    def test_marker_missing_returns_true(self, marker, downloads):
        """First run: no marker means we must build + sync."""
        assert not marker.exists()
        assert changes.changes_detected(marker, downloads, None) is True

    def test_watched_file_newer_than_marker_returns_true(self, marker, downloads):
        marker.write_text("old")
        # force marker mtime into the past
        past = time.time() - 3600
        os.utime(marker, (past, past))

        csv = downloads / "Accounts_History_latest.csv"
        csv.write_text("data")
        # csv mtime = now > past
        assert changes.changes_detected(marker, downloads, None) is True

    def test_qfx_newer_than_marker_returns_true(self, marker, downloads):
        marker.write_text("old")
        os.utime(marker, (time.time() - 3600,) * 2)
        qfx = downloads / "Bloomberg.Download_2026.qfx"
        qfx.write_text("qfx")
        assert changes.changes_detected(marker, downloads, None) is True

    def test_robinhood_newer_than_marker_returns_true(self, marker, downloads):
        marker.write_text("old")
        os.utime(marker, (time.time() - 3600,) * 2)
        rh = downloads / "Robinhood_history.csv"
        rh.write_text("rh")
        assert changes.changes_detected(marker, downloads, None) is True

    def test_no_newer_files_returns_false(self, marker, downloads):
        # Write file FIRST (stale), then refresh marker so marker > file mtime.
        csv = downloads / "Accounts_History_old.csv"
        csv.write_text("old")
        past = time.time() - 3600
        os.utime(csv, (past, past))

        marker.write_text("new")
        # marker's mtime is now (fresh), files are 1h old → no change
        assert changes.changes_detected(marker, downloads, None) is False

    def test_empty_downloads_returns_false(self, marker, downloads):
        marker.write_text("new")
        assert changes.changes_detected(marker, downloads, None) is False

    def test_qianji_db_newer_returns_true(self, marker, downloads, qianji_db_file):
        marker.write_text("old")
        os.utime(marker, (time.time() - 3600,) * 2)
        qianji_db_file.write_text("db")
        assert changes.changes_detected(marker, downloads, qianji_db_file) is True

    def test_portfolio_positions_IS_watched(self, marker, downloads):
        """Portfolio_Positions_*.csv IS watched (re-enabled in S5 to drive the [3b] gate)."""
        marker.write_text("old")
        os.utime(marker, (time.time() - 3600,) * 2)
        pp = downloads / "Portfolio_Positions_Apr-12-2026.csv"
        pp.write_text("positions")
        assert changes.changes_detected(marker, downloads, None) is True

    def test_missing_downloads_dir_returns_false(self, marker, tmp_path):
        marker.write_text("new")
        os.utime(marker, (time.time() - 3600,) * 2)
        nonexistent = tmp_path / "nope"
        assert changes.changes_detected(marker, nonexistent, None) is False


# ── needs_catchup() ───────────────────────────────────────────────────────────

class TestNeedsCatchup:
    """Guards the silent-skip gap: run even without CSV changes when the DB
    has drifted behind the wall-clock trading day."""

    def _seed_computed_daily(self, tmp_path, last_date: str):
        from etl.db import get_connection, init_db
        db = tmp_path / "tm.db"
        init_db(db)
        conn = get_connection(db)
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES (?, 100000, 55000, 15000, 3000, 27000)",
            (last_date,),
        )
        conn.commit()
        conn.close()
        return db

    def test_empty_db_needs_catchup(self, tmp_path):
        from etl.db import init_db
        db = tmp_path / "tm.db"
        init_db(db)
        from datetime import date
        assert changes.needs_catchup(db, today=date(2026, 4, 14)) is True

    def test_fresh_db_skips(self, tmp_path):
        from datetime import date
        db = self._seed_computed_daily(tmp_path, "2026-04-13")
        assert changes.needs_catchup(db, today=date(2026, 4, 14)) is False

    def test_db_4_days_behind_still_ok(self, tmp_path):
        """Standard long weekend (Fri→Tue = 4 days): tolerable."""
        from datetime import date
        db = self._seed_computed_daily(tmp_path, "2026-04-10")
        assert changes.needs_catchup(db, today=date(2026, 4, 14)) is False

    def test_db_5_days_behind_triggers_catchup(self, tmp_path):
        from datetime import date
        db = self._seed_computed_daily(tmp_path, "2026-04-09")
        assert changes.needs_catchup(db, today=date(2026, 4, 14)) is True

    def test_missing_db_file_triggers_catchup(self, tmp_path):
        from datetime import date
        nonexistent = tmp_path / "does-not-exist.db"
        assert changes.needs_catchup(nonexistent, today=date(2026, 4, 14)) is True


# ── parse_args() ──────────────────────────────────────────────────────────────

class TestParseArgs:
    def test_no_args_defaults_all_false(self):
        ns = runner.parse_args([])
        assert ns.force is False
        assert ns.dry_run is False
        assert ns.local is False

    def test_force(self):
        ns = runner.parse_args(["--force"])
        assert ns.force is True

    def test_dry_run(self):
        ns = runner.parse_args(["--dry-run"])
        assert ns.dry_run is True

    def test_local(self):
        ns = runner.parse_args(["--local"])
        assert ns.local is True

    def test_combined(self):
        ns = runner.parse_args(["--force", "--dry-run", "--local"])
        assert (ns.force, ns.dry_run, ns.local) == (True, True, True)

    def test_unknown_flag_exits(self):
        with pytest.raises(SystemExit):
            runner.parse_args(["--bogus"])

    def test_entry_point_script_reexports_parse_args(self):
        """``scripts/run_automation.py`` must continue to expose ``parse_args``
        so the Task Scheduler shim (run_portal_sync.ps1) keeps working even if
        a caller imports the script directly."""
        assert run_automation.parse_args is runner.parse_args


# ── Exit-code mapping ─────────────────────────────────────────────────────────

class _FakeRun:
    """Sequence of canned return codes, one per run_python_script call."""
    def __init__(self, codes: list[int]):
        self.codes = list(codes)
        self.calls: list[tuple[Path, tuple[str, ...]]] = []

    def __call__(self, script: Path, *args: str) -> int:
        self.calls.append((script, args))
        return self.codes.pop(0) if self.codes else 0


class TestExitCodeMapping:
    def _invoke(self, argv, codes, monkeypatch, tmp_path, downloads_seed=None):
        """Run main() with a fake run_python_script and isolated marker path.

        downloads_seed: iterable of filenames to create in the isolated Downloads
        dir before invocation (used to simulate a fresh Portfolio_Positions CSV
        for [3b] gate testing).
        """
        fake = _FakeRun(codes)
        monkeypatch.setattr(runner, "run_python_script", fake)
        monkeypatch.setattr(runner, "MARKER", tmp_path / ".last_run")
        monkeypatch.setattr(runner, "get_log_dir", lambda: tmp_path / "logs")
        # Isolate DB path so capture() sees a missing / empty file rather than the real one.
        monkeypatch.setenv("PORTAL_DB_PATH", str(tmp_path / "timemachine.db"))
        # Isolate from the real ~/Downloads so [3b] doesn't pick up real CSVs.
        iso_downloads = tmp_path / "iso_downloads"
        iso_downloads.mkdir(exist_ok=True)
        for fname in downloads_seed or ():
            (iso_downloads / fname).write_text("stub")
        monkeypatch.setattr(runner, "get_downloads_dir", lambda: iso_downloads)
        monkeypatch.setattr(runner, "get_qianji_db_path", lambda: None)
        # Ensure no network pings + no email (env vars unset by default).
        monkeypatch.setenv("PORTAL_HEALTHCHECK_URL", "https://hc.example/dummy")
        monkeypatch.delenv("PORTAL_SMTP_USER", raising=False)
        monkeypatch.delenv("PORTAL_SMTP_PASSWORD", raising=False)
        # Force path so change detection is bypassed (we always pass --force)
        rc = run_automation.main(argv)
        return rc, fake

    def test_all_ok_returns_0(self, monkeypatch, tmp_path):
        rc, fake = self._invoke(["--force"], [0, 0, 0, 0], monkeypatch, tmp_path)
        assert rc == EXIT_OK
        scripts = [c[0].name for c in fake.calls]
        assert scripts == ["build_timemachine_db.py", "r2_artifacts.py", "r2_artifacts.py", "r2_artifacts.py"]
        assert fake.calls[1][1] == ("export",)
        assert fake.calls[2][1] == ("verify",)
        assert fake.calls[3][1] == ("publish", "--remote")

    def test_build_fail_returns_1(self, monkeypatch, tmp_path):
        rc, fake = self._invoke(["--force"], [5], monkeypatch, tmp_path)
        assert rc == EXIT_BUILD_FAIL
        assert [c[0].name for c in fake.calls] == ["build_timemachine_db.py"]

    def test_artifact_export_fail_returns_2(self, monkeypatch, tmp_path):
        rc, fake = self._invoke(["--force"], [0, 7], monkeypatch, tmp_path)
        assert rc == EXIT_PARITY_FAIL
        assert [c[0].name for c in fake.calls] == ["build_timemachine_db.py", "r2_artifacts.py"]
        assert fake.calls[1][1] == ("export",)

    def test_artifact_verify_fail_returns_2(self, monkeypatch, tmp_path):
        rc, fake = self._invoke(["--force"], [0, 0, 7], monkeypatch, tmp_path)
        assert rc == EXIT_PARITY_FAIL
        assert [c[0].name for c in fake.calls] == ["build_timemachine_db.py", "r2_artifacts.py", "r2_artifacts.py"]
        assert fake.calls[2][1] == ("verify",)

    def test_sync_fail_returns_3(self, monkeypatch, tmp_path):
        rc, _ = self._invoke(["--force"], [0, 0, 0, 9], monkeypatch, tmp_path)
        assert rc == EXIT_SYNC_FAIL

    def test_local_publishes_to_local_r2(self, monkeypatch, tmp_path):
        rc, fake = self._invoke(["--force", "--local"], [0, 0, 0, 0], monkeypatch, tmp_path)
        assert rc == EXIT_OK
        names = [c[0].name for c in fake.calls]
        assert names == ["build_timemachine_db.py", "r2_artifacts.py", "r2_artifacts.py", "r2_artifacts.py"]
        assert fake.calls[-1][1] == ("publish", "--local")

    def test_dry_run_skips_sync(self, monkeypatch, tmp_path):
        rc, fake = self._invoke(["--force", "--dry-run"], [0, 0, 0], monkeypatch, tmp_path)
        assert rc == EXIT_OK
        names = [c[0].name for c in fake.calls]
        assert names == ["build_timemachine_db.py", "r2_artifacts.py", "r2_artifacts.py"]
        assert fake.calls[-1][1] == ("verify",)

    def test_success_writes_marker(self, monkeypatch, tmp_path):
        marker = tmp_path / ".last_run"
        assert not marker.exists()
        rc, _ = self._invoke(["--force"], [0, 0, 0, 0], monkeypatch, tmp_path)
        assert rc == 0
        assert marker.exists()
        assert marker.read_text().strip()  # non-empty ISO timestamp

    def test_dry_run_does_not_write_marker(self, monkeypatch, tmp_path):
        """--dry-run must NOT update marker — otherwise the next non-dry-run
        is short-circuited by change-detection thinking the DB is fresh.
        """
        marker = tmp_path / ".last_run"
        assert not marker.exists()
        rc, _ = self._invoke(["--force", "--dry-run"], [0, 0, 0], monkeypatch, tmp_path)
        assert rc == EXIT_OK
        assert not marker.exists(), (
            "dry-run wrote the marker; next real sync would be skipped"
        )

    def test_no_changes_returns_0_without_invoking_build(self, monkeypatch, tmp_path):
        """Without --force: no CSV changes AND DB fresh → exit 0, no subprocess."""
        from datetime import date

        from etl.db import get_connection, init_db

        fake = _FakeRun([])
        monkeypatch.setattr(runner, "run_python_script", fake)
        monkeypatch.setattr(runner, "MARKER", tmp_path / ".last_run")
        monkeypatch.setattr(runner, "get_log_dir", lambda: tmp_path / "logs")
        monkeypatch.setattr(runner, "get_downloads_dir", lambda: tmp_path / "empty_downloads")
        monkeypatch.setattr(runner, "get_qianji_db_path", lambda: None)
        monkeypatch.setenv("PORTAL_HEALTHCHECK_URL", "https://hc.example/dummy")
        (tmp_path / "empty_downloads").mkdir()
        (tmp_path / ".last_run").write_text("seeded")

        # Seed a fresh DB at the PORTAL_DB_PATH location so needs_catchup() returns False.
        db_path = tmp_path / "tm.db"
        monkeypatch.setenv("PORTAL_DB_PATH", str(db_path))
        init_db(db_path)
        conn = get_connection(db_path)
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES (?, 100000, 55000, 15000, 3000, 27000)",
            (date.today().isoformat(),),
        )
        conn.commit()
        conn.close()

        rc = run_automation.main([])
        assert rc == EXIT_OK
        assert fake.calls == []

    def test_positions_gate_runs_when_fresh_csv_present(self, monkeypatch, tmp_path):
        """[3b] runs verify_positions.py when a fresh Portfolio_Positions CSV is in Downloads."""
        rc, fake = self._invoke(
            ["--force"], [0, 0, 0, 0, 0], monkeypatch, tmp_path,
            downloads_seed=("Portfolio_Positions_Apr-07-2026.csv",),
        )
        assert rc == EXIT_OK
        names = [c[0].name for c in fake.calls]
        assert names == [
            "build_timemachine_db.py", "verify_positions.py",
            "r2_artifacts.py", "r2_artifacts.py", "r2_artifacts.py",
        ]
        # verify_positions invoked with --positions <path>
        verify_args = fake.calls[1][1]
        assert verify_args[0] == "--positions"
        assert verify_args[1].endswith("Portfolio_Positions_Apr-07-2026.csv")

    def test_positions_gate_skipped_when_no_csv(self, monkeypatch, tmp_path):
        """[3b] is skipped (not failed) when no Portfolio_Positions CSV is present."""
        rc, fake = self._invoke(["--force"], [0, 0, 0, 0], monkeypatch, tmp_path)
        assert rc == EXIT_OK
        names = [c[0].name for c in fake.calls]
        assert names == ["build_timemachine_db.py", "r2_artifacts.py", "r2_artifacts.py", "r2_artifacts.py"]

    def test_positions_fail_returns_4(self, monkeypatch, tmp_path):
        """verify_positions non-zero blocks sync with exit code 4."""
        rc, fake = self._invoke(
            ["--force"], [0, 1], monkeypatch, tmp_path,
            downloads_seed=("Portfolio_Positions_Apr-07-2026.csv",),
        )
        assert rc == EXIT_POSITIONS_FAIL
        names = [c[0].name for c in fake.calls]
        assert names == ["build_timemachine_db.py", "verify_positions.py"]
        # Publish must NOT have run.

    def test_positions_gate_skipped_in_local_mode(self, monkeypatch, tmp_path):
        """--local skips the positions gate and publishes to local R2."""
        rc, fake = self._invoke(
            ["--force", "--local"], [0, 0, 0, 0], monkeypatch, tmp_path,
            downloads_seed=("Portfolio_Positions_Apr-07-2026.csv",),
        )
        assert rc == EXIT_OK
        names = [c[0].name for c in fake.calls]
        assert names == ["build_timemachine_db.py", "r2_artifacts.py", "r2_artifacts.py", "r2_artifacts.py"]


# ── find_new_positions_csv() ──────────────────────────────────────────────────

class TestFindNewPositionsCSV:
    def test_returns_none_when_downloads_missing(self, tmp_path):
        downloads = tmp_path / "nope"
        marker = tmp_path / ".last_run"
        assert changes.find_new_positions_csv(downloads, marker) is None

    def test_returns_none_when_no_matching_files(self, tmp_path):
        downloads = tmp_path / "dl"
        downloads.mkdir()
        marker = tmp_path / ".last_run"
        (downloads / "Accounts_History.csv").write_text("x")
        assert changes.find_new_positions_csv(downloads, marker) is None

    def test_returns_csv_when_marker_missing(self, tmp_path):
        downloads = tmp_path / "dl"
        downloads.mkdir()
        marker = tmp_path / ".last_run"  # does not exist
        f = downloads / "Portfolio_Positions_Apr-07-2026.csv"
        f.write_text("x")
        result = changes.find_new_positions_csv(downloads, marker)
        assert result == f

    def test_returns_none_when_csv_older_than_marker(self, tmp_path):
        downloads = tmp_path / "dl"
        downloads.mkdir()
        marker = tmp_path / ".last_run"
        f = downloads / "Portfolio_Positions_Apr-07-2026.csv"
        f.write_text("x")
        # Age the CSV into the past.
        past = time.time() - 3600
        os.utime(f, (past, past))
        marker.write_text("fresh")
        assert changes.find_new_positions_csv(downloads, marker) is None

    def test_returns_newest_csv_when_multiple_fresh(self, tmp_path):
        downloads = tmp_path / "dl"
        downloads.mkdir()
        marker = tmp_path / ".last_run"
        marker.write_text("old")
        os.utime(marker, (time.time() - 7200,) * 2)
        older = downloads / "Portfolio_Positions_Apr-03-2026.csv"
        newer = downloads / "Portfolio_Positions_Apr-07-2026.csv"
        older.write_text("x")
        os.utime(older, (time.time() - 1800,) * 2)
        newer.write_text("x")  # mtime = now
        assert changes.find_new_positions_csv(downloads, marker) == newer


# ── Runner requires PORTAL_HEALTHCHECK_URL ────────────────────────────────────


class TestRunnerWarnsOnMissingHealthcheckUrl:
    """B3 (softened): constructing a Runner without ``PORTAL_HEALTHCHECK_URL``
    must log a loud warning but still succeed. Hard fail was too aggressive —
    users without healthchecks.io setup would get stuck."""

    def test_runner_init_warns_when_url_unset(self, monkeypatch, caplog):
        monkeypatch.delenv("PORTAL_HEALTHCHECK_URL", raising=False)
        args = runner.parse_args(["--force"])
        with caplog.at_level(logging.WARNING, logger="etl.automation.runner"):
            r = runner.Runner(args)
        assert any("PORTAL_HEALTHCHECK_URL" in rec.message for rec in caplog.records)
        assert r.args.force is True

    def test_runner_init_silent_when_url_set(self, monkeypatch, caplog):
        monkeypatch.setenv("PORTAL_HEALTHCHECK_URL", "https://hc.example/abc")
        args = runner.parse_args(["--force"])
        with caplog.at_level(logging.WARNING, logger="etl.automation.runner"):
            r = runner.Runner(args)
        assert not any("PORTAL_HEALTHCHECK_URL" in rec.message for rec in caplog.records)
        assert r.args.force is True


# ── ping_healthcheck() ────────────────────────────────────────────────────────

class TestPingHealthcheck:
    def test_no_op_when_url_unset(self, monkeypatch):
        """Low-level contract: ``notify.ping_healthcheck`` silently no-ops
        when the env var is unset. Runner-level enforcement happens in
        :class:`TestRunnerRequiresHealthcheckUrl`; ``ping_healthcheck`` itself
        stays tolerant for any other caller.
        """
        monkeypatch.delenv("PORTAL_HEALTHCHECK_URL", raising=False)
        # Should return without raising and without calling urlopen.
        with patch("urllib.request.urlopen") as mock_open:
            notify.ping_healthcheck()
            notify.ping_healthcheck("start")
            notify.ping_healthcheck("fail")
        mock_open.assert_not_called()

    def test_pings_when_url_set(self, monkeypatch):
        monkeypatch.setenv("PORTAL_HEALTHCHECK_URL", "https://hc.example/abc")
        with patch("urllib.request.urlopen") as mock_open:
            notify.ping_healthcheck()
            notify.ping_healthcheck("start")
            notify.ping_healthcheck("fail")
        assert mock_open.call_count == 3
        urls = [call.args[0] for call in mock_open.call_args_list]
        assert urls == [
            "https://hc.example/abc",
            "https://hc.example/abc/start",
            "https://hc.example/abc/fail",
        ]

    def test_ping_swallows_errors(self, monkeypatch):
        monkeypatch.setenv("PORTAL_HEALTHCHECK_URL", "https://hc.example/abc")
        import urllib.error

        def boom(*a, **kw):
            raise urllib.error.URLError("network down")

        with patch("urllib.request.urlopen", side_effect=boom):
            # Must not raise — healthcheck failure is never fatal.
            notify.ping_healthcheck("start")


# ── Path helpers ──────────────────────────────────────────────────────────────

class TestPathHelpers:
    def test_downloads_override_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PORTAL_DOWNLOADS", str(tmp_path))
        assert paths.get_downloads_dir() == tmp_path

    def test_downloads_fallback_userprofile(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PORTAL_DOWNLOADS", raising=False)
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        assert paths.get_downloads_dir() == tmp_path / "Downloads"

    def test_qianji_db_path_none_without_appdata(self, monkeypatch):
        monkeypatch.delenv("APPDATA", raising=False)
        assert paths.get_qianji_db_path() is None

    def test_qianji_db_path_uses_appdata(self, monkeypatch, tmp_path):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        p = paths.get_qianji_db_path()
        assert p is not None
        assert p.is_relative_to(tmp_path)
        assert p.name == "qianjiapp.db"

    def test_log_dir_uses_localappdata(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert paths.get_log_dir() == tmp_path / "portal" / "logs"

    def test_log_dir_fallback_non_windows(self, monkeypatch):
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        p = paths.get_log_dir()
        assert p.parts[-2:] == ("portal", "logs")

    def test_db_path_override_env(self, monkeypatch, tmp_path):
        override = tmp_path / "custom.db"
        monkeypatch.setenv("PORTAL_DB_PATH", str(override))
        assert paths.get_db_path() == override

    def test_db_path_default_under_data_dir(self, monkeypatch):
        monkeypatch.delenv("PORTAL_DB_PATH", raising=False)
        p = paths.get_db_path()
        assert p.name == "timemachine.db"
        assert p.parent == paths.DATA_DIR


# ── Email notifications ───────────────────────────────────────────────────────


class TestEmailNotifications:
    """Integration tests for the email-send branch of Runner.run().

    Strategy: monkeypatch ``etl.automation.notify.send`` (the low-level SMTP call)
    and ``etl.automation.receipt.capture`` (to inject before/after snapshots). This
    lets us assert send-or-skip policy without touching real SMTP or SQLite.
    """

    def _invoke_with_email(
        self,
        argv,
        codes,
        monkeypatch,
        tmp_path,
        *,
        snapshot_before=None,
        snapshot_after=None,
        send_side_effect=None,
        disable_email=False,
        downloads_seed=None,
    ):
        """Same as TestExitCodeMapping._invoke but with email-config env vars + send mock."""
        from etl.automation.receipt import SyncSnapshot

        fake = _FakeRun(codes)
        monkeypatch.setattr(runner, "run_python_script", fake)
        monkeypatch.setattr(runner, "MARKER", tmp_path / ".last_run")
        monkeypatch.setattr(runner, "get_log_dir", lambda: tmp_path / "logs")
        monkeypatch.setenv("PORTAL_DB_PATH", str(tmp_path / "timemachine.db"))
        iso_downloads = tmp_path / "iso_downloads"
        iso_downloads.mkdir(exist_ok=True)
        for fname in downloads_seed or ():
            (iso_downloads / fname).write_text("stub")
        monkeypatch.setattr(runner, "get_downloads_dir", lambda: iso_downloads)
        monkeypatch.setattr(runner, "get_qianji_db_path", lambda: None)
        monkeypatch.setenv("PORTAL_HEALTHCHECK_URL", "https://hc.example/dummy")

        if disable_email:
            monkeypatch.delenv("PORTAL_SMTP_USER", raising=False)
            monkeypatch.delenv("PORTAL_SMTP_PASSWORD", raising=False)
        else:
            monkeypatch.setenv("PORTAL_SMTP_USER", "me@gmail.com")
            monkeypatch.setenv("PORTAL_SMTP_PASSWORD", "apppw")

        # Capture is called 1+ times (once before, possibly once after). Rotate
        # through: [before, after, after, ...].
        snapshots = [snapshot_before or SyncSnapshot()]
        if snapshot_after is not None:
            snapshots.append(snapshot_after)

        def fake_capture(_path):
            if len(snapshots) > 1:
                return snapshots.pop(0)
            return snapshots[0]

        monkeypatch.setattr(runner, "capture", fake_capture)

        # Mock the SMTP send path (imported into notify as ``send``).
        sent_calls = []

        def fake_send(subject, html, text, config):
            sent_calls.append({"subject": subject, "html": html, "text": text, "config": config})
            if send_side_effect:
                raise send_side_effect

        monkeypatch.setattr(notify, "send", fake_send)

        rc = run_automation.main(argv)
        return rc, fake, sent_calls

    def test_success_with_no_diff_still_emails(self, monkeypatch, tmp_path):
        """Success email is sent even when snapshot diff is empty."""
        from etl.automation.receipt import SyncSnapshot

        rc, _, sent = self._invoke_with_email(
            ["--force"], [0, 0, 0, 0], monkeypatch, tmp_path,
            snapshot_before=SyncSnapshot(),
            snapshot_after=SyncSnapshot(),  # identical
        )
        assert rc == EXIT_OK
        assert len(sent) == 1
        assert sent[0]["subject"].startswith("[Portal Sync] OK")

    def test_row_count_change_sends_email_summary(self, monkeypatch, tmp_path):
        """Success with a row-count delta -> one compact summary email."""
        from etl.automation.receipt import SyncSnapshot

        before = SyncSnapshot(row_counts={"fidelityTxns": 0})
        after = SyncSnapshot(row_counts={"fidelityTxns": 1})
        rc, _, sent = self._invoke_with_email(
            ["--force"], [0, 0, 0, 0], monkeypatch, tmp_path,
            snapshot_before=before, snapshot_after=after,
        )
        assert rc == EXIT_OK
        assert len(sent) == 1
        assert sent[0]["subject"].startswith("[Portal Sync] OK")
        assert "fidelityTxns: 0 -> 1 (+1)" in sent[0]["text"]

    def test_build_failure_sends_email_with_exit_1(self, monkeypatch, tmp_path):
        """Build fail -> email even though no snapshot_after available."""
        from etl.automation.receipt import SyncSnapshot

        rc, _, sent = self._invoke_with_email(
            ["--force"], [5], monkeypatch, tmp_path,
            snapshot_before=SyncSnapshot(),
            snapshot_after=None,  # build failed — no after snapshot
        )
        assert rc == EXIT_BUILD_FAIL
        assert len(sent) == 1
        assert "FAIL" in sent[0]["subject"]
        assert "BUILD FAILED" in sent[0]["subject"]
        # Body should mention the error
        assert "build_timemachine_db.py" in sent[0]["text"]
        # P1 regression: failure emails must include Duration.
        assert "Duration:" in sent[0]["text"]

    def test_positions_gate_failure_email_includes_duration(self, monkeypatch, tmp_path):
        """P1 regression: positions-gate failure email must include Duration.

        Prior to the fix, ``_report_stage_failure`` was called with
        ``include_started_at=False`` on this branch and the email had no
        ``Duration: …`` line — the only failure email that didn't.
        """
        from etl.automation.receipt import SyncSnapshot

        rc, _, sent = self._invoke_with_email(
            ["--force"], [0, 1], monkeypatch, tmp_path,
            snapshot_before=SyncSnapshot(),
            snapshot_after=None,
            downloads_seed=("Portfolio_Positions_Apr-07-2026.csv",),
        )
        assert rc == EXIT_POSITIONS_FAIL
        assert len(sent) == 1
        assert "verify_positions.py" in sent[0]["text"]
        assert "Duration:" in sent[0]["text"]

    def test_email_disabled_no_smtp_activity(self, monkeypatch, tmp_path, capsys):
        """No SMTP_USER/PASSWORD -> no send call, log notes disabled."""
        from etl.automation.receipt import SyncSnapshot

        rc, _, sent = self._invoke_with_email(
            ["--force"], [0, 0, 0, 0], monkeypatch, tmp_path,
            snapshot_before=SyncSnapshot(),
            snapshot_after=SyncSnapshot(row_counts={"fidelityTxns": 1}),
            disable_email=True,
        )
        assert rc == EXIT_OK
        assert sent == []  # No SMTP activity even with meaningful changes
        # Runner installs its own handlers on stdout via setup_logging
        captured = capsys.readouterr()
        assert "Email reporting: disabled" in captured.out

    def test_email_send_failure_does_not_fail_sync(self, monkeypatch, tmp_path, capsys):
        """SMTP error is logged but must not affect the exit code."""
        from etl.automation.receipt import SyncSnapshot

        rc, _, sent = self._invoke_with_email(
            ["--force"], [0, 0, 0, 0], monkeypatch, tmp_path,
            snapshot_before=SyncSnapshot(),
            snapshot_after=SyncSnapshot(row_counts={"fidelityTxns": 1}),
            send_side_effect=ConnectionRefusedError("smtp down"),
        )
        # Publish must still return OK even though email throw
        assert rc == EXIT_OK
        assert len(sent) == 1
        captured = capsys.readouterr()
        assert "Email send FAILED" in captured.out


# ── extract_validation_warnings() ─────────────────────────────────────────────


class TestExtractValidationWarnings:
    def setup_method(self) -> None:
        runner._reset_script_output_buffer()

    def teardown_method(self) -> None:
        runner._reset_script_output_buffer()

    def test_returns_empty_without_buffer(self):
        assert notify.extract_validation_warnings() == []

    def test_parses_warning_lines(self):
        warnings = notify.extract_validation_warnings([
            "2026-04-12 INFO [2] build",
            "2026-04-12 WARNING: day_over_day 2023-07-04 -> 2023-07-05: 15.7% change",
            "2026-04-12 INFO done",
            "2026-04-12 WARNING date_gaps 8-day gap between dates",
        ])
        assert len(warnings) == 2
        assert "15.7%" in warnings[0]
        assert "date_gaps" in warnings[1]

    def test_skips_healthcheck_failures(self):
        assert notify.extract_validation_warnings([
            "2026-04-12 WARNING: healthcheck ping failed (ignored): network down",
        ]) == []

    def test_extract_warnings_dedup(self):
        """Exact-duplicate WARNING lines within the current run collapse to one."""
        warnings = notify.extract_validation_warnings([
            "2026-04-12T12:00:01 WARNING: day_over_day 2023-07-04 -> 2023-07-05: 15.7% change",
            "2026-04-12T12:00:02 WARNING: day_over_day 2023-07-04 -> 2023-07-05: 15.7% change",
            "2026-04-12T12:00:03 WARNING: day_over_day 2023-07-04 -> 2023-07-05: 15.7% change",
        ])
        assert len(warnings) == 1
        assert "15.7%" in warnings[0]

    def test_extract_warnings_uses_capture_buffer(self):
        """Simulate run_python_script appending output during one run."""
        runner._SCRIPT_OUTPUT_BUFFER.extend([
            "2026-04-12T12:00:00 INFO [2] build",
            "2026-04-12T12:00:01 WARNING: day_over_day 2023-07-04 -> 2023-07-05: 15.7% change",
            "2026-04-12T12:00:01 WARNING: day_over_day 2023-07-04 -> 2023-07-05: 15.7% change",
            "2026-04-12T12:00:02 INFO done",
        ])
        warnings = notify.extract_validation_warnings(runner.get_script_output_buffer())
        assert len(warnings) == 1  # deduped
        assert "15.7%" in warnings[0]

    def test_extract_warnings_buffer_scopes_to_current_main_run(
        self, monkeypatch, tmp_path, caplog
    ):
        """End-to-end: Runner.run() resets the buffer at start → a second
        invocation in the same process does NOT see warnings from the first."""
        from etl.automation.receipt import SyncSnapshot

        # Pre-seed the buffer as if a prior run had left stale warnings around.
        runner._SCRIPT_OUTPUT_BUFFER.extend([
            "2026-04-12T08:00:01 WARNING: STALE bad_data found",
        ])

        # Set up a successful main() invocation that doesn't actually call any
        # subprocess scripts — we just want to verify the reset + downstream
        # extractor sees an empty buffer.
        class _Fake:
            def __call__(self, script, *args):
                return 0

        monkeypatch.setattr(runner, "run_python_script", _Fake())
        monkeypatch.setattr(runner, "MARKER", tmp_path / ".last_run")
        monkeypatch.setattr(runner, "get_log_dir", lambda: tmp_path / "logs")
        monkeypatch.setenv("PORTAL_DB_PATH", str(tmp_path / "timemachine.db"))
        iso_downloads = tmp_path / "iso_downloads"
        iso_downloads.mkdir()
        monkeypatch.setattr(runner, "get_downloads_dir", lambda: iso_downloads)
        monkeypatch.setattr(runner, "get_qianji_db_path", lambda: None)
        monkeypatch.setenv("PORTAL_HEALTHCHECK_URL", "https://hc.example/dummy")
        monkeypatch.delenv("PORTAL_SMTP_USER", raising=False)
        monkeypatch.delenv("PORTAL_SMTP_PASSWORD", raising=False)

        # Stub capture so no real DB is needed.
        monkeypatch.setattr(runner, "capture", lambda _p: SyncSnapshot())

        rc = run_automation.main(["--force"])
        assert rc == EXIT_OK
        # Buffer should have been reset at start-of-run; since _Fake() did
        # not write anything to the buffer, it stays empty post-run.
        assert runner.get_script_output_buffer() == []
