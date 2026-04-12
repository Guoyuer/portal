"""Tests for scripts/run_automation.py — the Python orchestrator that replaces
the old PowerShell logic. Covers change detection, CLI parsing, exit-code
mapping, and healthcheck behaviour."""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts import run_automation  # noqa: E402

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
    # Clear handlers attached by run_automation.setup_logging() in prior tests.
    for h in list(logging.getLogger().handlers):
        logging.getLogger().removeHandler(h)
    caplog.set_level(logging.INFO)
    yield
    for h in list(logging.getLogger().handlers):
        logging.getLogger().removeHandler(h)


# ── changes_detected() ────────────────────────────────────────────────────────

class TestChangesDetected:
    def test_marker_missing_returns_true(self, marker, downloads):
        """First run: no marker means we must build + sync."""
        assert not marker.exists()
        assert run_automation.changes_detected(marker, downloads, None) is True

    def test_watched_file_newer_than_marker_returns_true(self, marker, downloads):
        marker.write_text("old")
        # force marker mtime into the past
        past = time.time() - 3600
        os.utime(marker, (past, past))

        csv = downloads / "Accounts_History_latest.csv"
        csv.write_text("data")
        # csv mtime = now > past
        assert run_automation.changes_detected(marker, downloads, None) is True

    def test_qfx_newer_than_marker_returns_true(self, marker, downloads):
        marker.write_text("old")
        os.utime(marker, (time.time() - 3600,) * 2)
        qfx = downloads / "Bloomberg.Download_2026.qfx"
        qfx.write_text("qfx")
        assert run_automation.changes_detected(marker, downloads, None) is True

    def test_robinhood_newer_than_marker_returns_true(self, marker, downloads):
        marker.write_text("old")
        os.utime(marker, (time.time() - 3600,) * 2)
        rh = downloads / "Robinhood_history.csv"
        rh.write_text("rh")
        assert run_automation.changes_detected(marker, downloads, None) is True

    def test_no_newer_files_returns_false(self, marker, downloads):
        # Write file FIRST (stale), then refresh marker so marker > file mtime.
        csv = downloads / "Accounts_History_old.csv"
        csv.write_text("old")
        past = time.time() - 3600
        os.utime(csv, (past, past))

        marker.write_text("new")
        # marker's mtime is now (fresh), files are 1h old → no change
        assert run_automation.changes_detected(marker, downloads, None) is False

    def test_empty_downloads_returns_false(self, marker, downloads):
        marker.write_text("new")
        assert run_automation.changes_detected(marker, downloads, None) is False

    def test_qianji_db_newer_returns_true(self, marker, downloads, qianji_db_file):
        marker.write_text("old")
        os.utime(marker, (time.time() - 3600,) * 2)
        qianji_db_file.write_text("db")
        assert run_automation.changes_detected(marker, downloads, qianji_db_file) is True

    def test_portfolio_positions_IS_watched(self, marker, downloads):
        """Portfolio_Positions_*.csv IS watched (re-enabled in S5 to drive the [3b] gate)."""
        marker.write_text("old")
        os.utime(marker, (time.time() - 3600,) * 2)
        pp = downloads / "Portfolio_Positions_Apr-12-2026.csv"
        pp.write_text("positions")
        assert run_automation.changes_detected(marker, downloads, None) is True

    def test_missing_downloads_dir_returns_false(self, marker, tmp_path):
        marker.write_text("new")
        os.utime(marker, (time.time() - 3600,) * 2)
        nonexistent = tmp_path / "nope"
        assert run_automation.changes_detected(marker, nonexistent, None) is False


# ── parse_args() ──────────────────────────────────────────────────────────────

class TestParseArgs:
    def test_no_args_defaults_all_false(self):
        ns = run_automation.parse_args([])
        assert ns.force is False
        assert ns.dry_run is False
        assert ns.local is False

    def test_force(self):
        ns = run_automation.parse_args(["--force"])
        assert ns.force is True

    def test_dry_run(self):
        ns = run_automation.parse_args(["--dry-run"])
        assert ns.dry_run is True

    def test_local(self):
        ns = run_automation.parse_args(["--local"])
        assert ns.local is True

    def test_combined(self):
        ns = run_automation.parse_args(["--force", "--dry-run", "--local"])
        assert (ns.force, ns.dry_run, ns.local) == (True, True, True)

    def test_unknown_flag_exits(self):
        with pytest.raises(SystemExit):
            run_automation.parse_args(["--bogus"])


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
        monkeypatch.setattr(run_automation, "run_python_script", fake)
        monkeypatch.setattr(run_automation, "_MARKER", tmp_path / ".last_run")
        monkeypatch.setattr(run_automation, "get_log_dir", lambda: tmp_path / "logs")
        # Isolate from the real ~/Downloads so [3b] doesn't pick up real CSVs.
        iso_downloads = tmp_path / "iso_downloads"
        iso_downloads.mkdir(exist_ok=True)
        for fname in downloads_seed or ():
            (iso_downloads / fname).write_text("stub")
        monkeypatch.setattr(run_automation, "get_downloads_dir", lambda: iso_downloads)
        monkeypatch.setattr(run_automation, "get_qianji_db_path", lambda: None)
        # Ensure no network pings
        monkeypatch.delenv("PORTAL_HEALTHCHECK_URL", raising=False)
        # Force path so change detection is bypassed (we always pass --force)
        rc = run_automation.main(argv)
        return rc, fake

    def test_all_ok_returns_0(self, monkeypatch, tmp_path):
        rc, fake = self._invoke(["--force"], [0, 0, 0], monkeypatch, tmp_path)
        assert rc == run_automation.EXIT_OK
        # Build, verify, sync — all three called.
        scripts = [c[0].name for c in fake.calls]
        assert scripts == ["build_timemachine_db.py", "verify_vs_prod.py", "sync_to_d1.py"]

    def test_build_fail_returns_1(self, monkeypatch, tmp_path):
        rc, fake = self._invoke(["--force"], [5], monkeypatch, tmp_path)
        assert rc == run_automation.EXIT_BUILD_FAIL
        assert [c[0].name for c in fake.calls] == ["build_timemachine_db.py"]

    def test_parity_fail_returns_2(self, monkeypatch, tmp_path):
        rc, fake = self._invoke(["--force"], [0, 7], monkeypatch, tmp_path)
        assert rc == run_automation.EXIT_PARITY_FAIL
        # Sync must NOT have been attempted after parity fails.
        assert [c[0].name for c in fake.calls] == ["build_timemachine_db.py", "verify_vs_prod.py"]

    def test_sync_fail_returns_3(self, monkeypatch, tmp_path):
        rc, _ = self._invoke(["--force"], [0, 0, 9], monkeypatch, tmp_path)
        assert rc == run_automation.EXIT_SYNC_FAIL

    def test_local_skips_verify_and_passes_local_to_sync(self, monkeypatch, tmp_path):
        rc, fake = self._invoke(["--force", "--local"], [0, 0], monkeypatch, tmp_path)
        assert rc == run_automation.EXIT_OK
        names = [c[0].name for c in fake.calls]
        assert names == ["build_timemachine_db.py", "sync_to_d1.py"]
        # sync invoked with --local arg
        assert fake.calls[-1][1] == ("--local",)

    def test_dry_run_skips_sync(self, monkeypatch, tmp_path):
        rc, fake = self._invoke(["--force", "--dry-run"], [0, 0], monkeypatch, tmp_path)
        assert rc == run_automation.EXIT_OK
        names = [c[0].name for c in fake.calls]
        assert names == ["build_timemachine_db.py", "verify_vs_prod.py"]

    def test_success_writes_marker(self, monkeypatch, tmp_path):
        marker = tmp_path / ".last_run"
        assert not marker.exists()
        rc, _ = self._invoke(["--force"], [0, 0, 0], monkeypatch, tmp_path)
        assert rc == 0
        assert marker.exists()
        assert marker.read_text().strip()  # non-empty ISO timestamp

    def test_no_changes_returns_0_without_invoking_build(self, monkeypatch, tmp_path):
        """Without --force: if change detection says no-change, exit 0 and don't call subprocesses."""
        fake = _FakeRun([])
        monkeypatch.setattr(run_automation, "run_python_script", fake)
        monkeypatch.setattr(run_automation, "_MARKER", tmp_path / ".last_run")
        monkeypatch.setattr(run_automation, "get_log_dir", lambda: tmp_path / "logs")
        monkeypatch.setattr(run_automation, "get_downloads_dir", lambda: tmp_path / "empty_downloads")
        monkeypatch.setattr(run_automation, "get_qianji_db_path", lambda: None)
        monkeypatch.delenv("PORTAL_HEALTHCHECK_URL", raising=False)
        (tmp_path / "empty_downloads").mkdir()
        # Seed a fresh marker — no watched files newer than it.
        (tmp_path / ".last_run").write_text("seeded")

        rc = run_automation.main([])
        assert rc == run_automation.EXIT_OK
        assert fake.calls == []

    def test_positions_gate_runs_when_fresh_csv_present(self, monkeypatch, tmp_path):
        """[3b] runs verify_positions.py when a fresh Portfolio_Positions CSV is in Downloads."""
        rc, fake = self._invoke(
            ["--force"], [0, 0, 0, 0], monkeypatch, tmp_path,
            downloads_seed=("Portfolio_Positions_Apr-07-2026.csv",),
        )
        assert rc == run_automation.EXIT_OK
        names = [c[0].name for c in fake.calls]
        assert names == [
            "build_timemachine_db.py", "verify_vs_prod.py",
            "verify_positions.py", "sync_to_d1.py",
        ]
        # verify_positions invoked with --positions <path>
        verify_args = fake.calls[2][1]
        assert verify_args[0] == "--positions"
        assert verify_args[1].endswith("Portfolio_Positions_Apr-07-2026.csv")

    def test_positions_gate_skipped_when_no_csv(self, monkeypatch, tmp_path):
        """[3b] is skipped (not failed) when no Portfolio_Positions CSV is present."""
        rc, fake = self._invoke(["--force"], [0, 0, 0], monkeypatch, tmp_path)
        assert rc == run_automation.EXIT_OK
        names = [c[0].name for c in fake.calls]
        assert names == ["build_timemachine_db.py", "verify_vs_prod.py", "sync_to_d1.py"]

    def test_positions_fail_returns_4(self, monkeypatch, tmp_path):
        """verify_positions non-zero blocks sync with exit code 4."""
        rc, fake = self._invoke(
            ["--force"], [0, 0, 1], monkeypatch, tmp_path,
            downloads_seed=("Portfolio_Positions_Apr-07-2026.csv",),
        )
        assert rc == run_automation.EXIT_POSITIONS_FAIL
        names = [c[0].name for c in fake.calls]
        assert names == ["build_timemachine_db.py", "verify_vs_prod.py", "verify_positions.py"]
        # Sync must NOT have run.

    def test_positions_gate_skipped_in_local_mode(self, monkeypatch, tmp_path):
        """--local skips both [3] parity and [3b] positions gates."""
        rc, fake = self._invoke(
            ["--force", "--local"], [0, 0], monkeypatch, tmp_path,
            downloads_seed=("Portfolio_Positions_Apr-07-2026.csv",),
        )
        assert rc == run_automation.EXIT_OK
        names = [c[0].name for c in fake.calls]
        assert names == ["build_timemachine_db.py", "sync_to_d1.py"]


# ── find_new_positions_csv() ──────────────────────────────────────────────────

class TestFindNewPositionsCSV:
    def test_returns_none_when_downloads_missing(self, tmp_path):
        downloads = tmp_path / "nope"
        marker = tmp_path / ".last_run"
        assert run_automation.find_new_positions_csv(downloads, marker) is None

    def test_returns_none_when_no_matching_files(self, tmp_path):
        downloads = tmp_path / "dl"
        downloads.mkdir()
        marker = tmp_path / ".last_run"
        (downloads / "Accounts_History.csv").write_text("x")
        assert run_automation.find_new_positions_csv(downloads, marker) is None

    def test_returns_csv_when_marker_missing(self, tmp_path):
        downloads = tmp_path / "dl"
        downloads.mkdir()
        marker = tmp_path / ".last_run"  # does not exist
        f = downloads / "Portfolio_Positions_Apr-07-2026.csv"
        f.write_text("x")
        result = run_automation.find_new_positions_csv(downloads, marker)
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
        assert run_automation.find_new_positions_csv(downloads, marker) is None

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
        assert run_automation.find_new_positions_csv(downloads, marker) == newer


# ── ping_healthcheck() ────────────────────────────────────────────────────────

class TestPingHealthcheck:
    def test_no_op_when_url_unset(self, monkeypatch):
        monkeypatch.delenv("PORTAL_HEALTHCHECK_URL", raising=False)
        # Should return without raising and without calling urlopen.
        with patch("urllib.request.urlopen") as mock_open:
            run_automation.ping_healthcheck()
            run_automation.ping_healthcheck("start")
            run_automation.ping_healthcheck("fail")
        mock_open.assert_not_called()

    def test_pings_when_url_set(self, monkeypatch):
        monkeypatch.setenv("PORTAL_HEALTHCHECK_URL", "https://hc.example/abc")
        with patch("urllib.request.urlopen") as mock_open:
            run_automation.ping_healthcheck()
            run_automation.ping_healthcheck("start")
            run_automation.ping_healthcheck("fail")
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
            run_automation.ping_healthcheck("start")


# ── Path helpers ──────────────────────────────────────────────────────────────

class TestPathHelpers:
    def test_downloads_override_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PORTAL_DOWNLOADS", str(tmp_path))
        assert run_automation.get_downloads_dir() == tmp_path

    def test_downloads_fallback_userprofile(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PORTAL_DOWNLOADS", raising=False)
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        assert run_automation.get_downloads_dir() == tmp_path / "Downloads"

    def test_qianji_db_path_none_without_appdata(self, monkeypatch):
        monkeypatch.delenv("APPDATA", raising=False)
        assert run_automation.get_qianji_db_path() is None

    def test_qianji_db_path_uses_appdata(self, monkeypatch, tmp_path):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        p = run_automation.get_qianji_db_path()
        assert p is not None
        assert p.is_relative_to(tmp_path)
        assert p.name == "qianjiapp.db"

    def test_log_dir_uses_localappdata(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert run_automation.get_log_dir() == tmp_path / "portal" / "logs"

    def test_log_dir_fallback_non_windows(self, monkeypatch):
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        p = run_automation.get_log_dir()
        assert p.parts[-2:] == ("portal", "logs")
