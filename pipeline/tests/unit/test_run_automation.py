"""Tests for scripts/run_automation.py orchestration."""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import etl.automation.changes as changes  # noqa: E402
import etl.automation.notify as notify  # noqa: E402
import etl.automation.runner as runner  # noqa: E402
from etl.automation._constants import (  # noqa: E402
    EXIT_BUILD_FAIL,
    EXIT_OK,
    EXIT_PARITY_FAIL,
    EXIT_POSITIONS_FAIL,
    EXIT_SYNC_FAIL,
)
from etl.automation.receipt import NetWorthPoint, SyncSnapshot  # noqa: E402
from scripts import run_automation  # noqa: E402
from tests.fixtures import connected_db, insert_computed_daily  # noqa: E402

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
    return tmp_path / "qianjiapp.db"


def _clear_root_handlers() -> None:
    for h in list(logging.getLogger().handlers):
        logging.getLogger().removeHandler(h)
        h.close()


@pytest.fixture(autouse=True)
def _silence_logs(caplog):
    _clear_root_handlers()
    caplog.set_level(logging.INFO)
    yield
    _clear_root_handlers()


# ── changes_detected() ────────────────────────────────────────────────────────


def _stale_marker(marker: Path) -> None:
    marker.write_text("old")
    os.utime(marker, (time.time() - 3600,) * 2)


def _write_file(path: Path, text: str = "x", *, age_seconds: int | None = None) -> Path:
    path.write_text(text)
    if age_seconds is not None:
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
    return path


def _seed_computed_daily(tmp_path: Path, last_date: str) -> Path:
    from etl.db import init_db

    db = tmp_path / "tm.db"
    init_db(db)
    with connected_db(db) as conn:
        insert_computed_daily(conn, last_date, 100000, us_equity=55000, non_us_equity=15000, crypto=3000, safe_net=27000)
    return db


class TestChangesDetected:
    def test_marker_missing_returns_true(self, marker, downloads):
        assert not marker.exists()
        assert changes.changes_detected(marker, downloads, downloads / "missing-qianji.db") is True

    @pytest.mark.parametrize(
        "filename",
        [
            "Accounts_History_latest.csv",
            "Bloomberg.Download_2026.qfx",
            "Robinhood_history.csv",
            "Portfolio_Positions_Apr-12-2026.csv",
        ],
    )
    def test_watched_file_newer_than_marker_returns_true(self, marker, downloads, filename):
        _stale_marker(marker)
        (downloads / filename).write_text("data")
        assert changes.changes_detected(marker, downloads, downloads / "missing-qianji.db") is True

    def test_no_newer_files_returns_false(self, marker, downloads):
        csv = downloads / "Accounts_History_old.csv"
        csv.write_text("old")
        past = time.time() - 3600
        os.utime(csv, (past, past))
        marker.write_text("new")
        assert changes.changes_detected(marker, downloads, downloads / "missing-qianji.db") is False

    def test_empty_downloads_returns_false(self, marker, downloads):
        marker.write_text("new")
        assert changes.changes_detected(marker, downloads, downloads / "missing-qianji.db") is False

    def test_qianji_db_newer_returns_true(self, marker, downloads, qianji_db_file):
        _stale_marker(marker)
        qianji_db_file.write_text("db")
        assert changes.changes_detected(marker, downloads, qianji_db_file) is True

    def test_missing_downloads_dir_returns_false(self, marker, tmp_path):
        _stale_marker(marker)
        assert changes.changes_detected(marker, tmp_path / "nope", tmp_path / "missing-qianji.db") is False


# ── needs_catchup() ───────────────────────────────────────────────────────────

class TestNeedsCatchup:
    @pytest.mark.parametrize(
        ("last_date", "expected"),
        [
            pytest.param("2026-04-13", False, id="fresh-1day"),
            pytest.param("2026-04-10", False, id="long-weekend-4days"),
            pytest.param("2026-04-09", True, id="5days-triggers"),
        ],
    )
    def test_seeded_db_catchup_window(self, tmp_path, last_date: str, expected: bool):
        db = _seed_computed_daily(tmp_path, last_date)
        assert changes.needs_catchup(db, today=date(2026, 4, 14)) is expected

    def test_empty_db_needs_catchup(self, tmp_path):
        from etl.db import init_db
        db = tmp_path / "tm.db"
        init_db(db)
        assert changes.needs_catchup(db, today=date(2026, 4, 14)) is True

    def test_missing_db_file_triggers_catchup(self, tmp_path):
        assert changes.needs_catchup(tmp_path / "missing.db", today=date(2026, 4, 14)) is True


# ── Exit-code mapping ─────────────────────────────────────────────────────────

class _FakeRun:
    def __init__(self, codes: list[int], outputs: list[list[str]] | None = None):
        self.codes = list(codes)
        self.outputs = [list(lines) for lines in outputs or []]
        self.calls: list[tuple[Path, tuple[str, ...]]] = []

    def __call__(self, script: Path, *args: str) -> tuple[int, list[str]]:
        self.calls.append((script, args))
        code = self.codes.pop(0) if self.codes else 0
        output = self.outputs.pop(0) if self.outputs else []
        return code, output


_BUILD = "build_timemachine_db.py"
_R2 = "r2_artifacts.py"
_VERIFY_POS = "verify_positions.py"


def _stub_runner_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    downloads_seed: tuple[str, ...] | None = None,
    *,
    email_enabled: bool = False,
) -> Path:
    marker = tmp_path / ".last_run"
    monkeypatch.setattr(runner, "MARKER", marker)
    monkeypatch.setattr(runner, "get_log_dir", lambda: tmp_path / "logs")
    monkeypatch.setenv("PORTAL_DB_PATH", str(tmp_path / "timemachine.db"))
    downloads = tmp_path / "iso_downloads"
    downloads.mkdir(exist_ok=True)
    for fname in downloads_seed or ():
        (downloads / fname).write_text("stub")
    monkeypatch.setattr(runner, "get_downloads_dir", lambda: downloads)
    monkeypatch.setattr(runner, "get_qianji_db_path", lambda: tmp_path / "missing-qianji.db")
    monkeypatch.setenv("PORTAL_HEALTHCHECK_URL", "https://hc.example/dummy")
    if email_enabled:
        monkeypatch.setenv("PORTAL_SMTP_USER", "me@gmail.com")
        monkeypatch.setenv("PORTAL_SMTP_PASSWORD", "apppw")
    else:
        monkeypatch.delenv("PORTAL_SMTP_USER", raising=False)
        monkeypatch.delenv("PORTAL_SMTP_PASSWORD", raising=False)
    return marker


class TestExitCodeMapping:
    def _invoke(self, argv, codes, monkeypatch, tmp_path, downloads_seed=None):
        fake = _FakeRun(codes)
        monkeypatch.setattr(runner, "run_python_script", fake)
        _stub_runner_env(monkeypatch, tmp_path, downloads_seed)
        rc = run_automation.main(argv)
        return rc, fake

    @pytest.mark.parametrize(
        ("argv", "codes", "downloads_seed", "expected_rc", "expected_scripts", "expected_last_args"),
        [
            pytest.param(
                ["--force"], [0, 0, 0], None,
                EXIT_OK, [_BUILD, _R2, _R2], ("publish", "--remote"),
                id="all-ok-publishes-remote",
            ),
            pytest.param(
                ["--force"], [5], None,
                EXIT_BUILD_FAIL, [_BUILD], None,
                id="build-fail-stops-pipeline",
            ),
            pytest.param(
                ["--force"], [0, 7], None,
                EXIT_PARITY_FAIL, [_BUILD, _R2], ("export",),
                id="export-fail-stops-after-export",
            ),
            pytest.param(
                ["--force", "--dry-run"], [0, 0, 7], None,
                EXIT_PARITY_FAIL, [_BUILD, _R2, _R2], ("verify",),
                id="dry-run-verify-fail-stops-after-verify",
            ),
            pytest.param(
                ["--force"], [0, 0, 9], None,
                EXIT_SYNC_FAIL, [_BUILD, _R2, _R2], None,
                id="sync-fail-after-publish-attempt",
            ),
            pytest.param(
                ["--force", "--dry-run"], [0, 0, 0], None,
                EXIT_OK, [_BUILD, _R2, _R2], ("verify",),
                id="dry-run-skips-publish",
            ),
            pytest.param(
                ["--force"], [0, 0, 0, 0], ("Portfolio_Positions_Apr-07-2026.csv",),
                EXIT_OK, [_BUILD, _VERIFY_POS, _R2, _R2], None,
                id="positions-gate-runs-when-fresh-csv",
            ),
            pytest.param(
                ["--force"], [0, 1], ("Portfolio_Positions_Apr-07-2026.csv",),
                EXIT_POSITIONS_FAIL, [_BUILD, _VERIFY_POS], None,
                id="positions-fail-blocks-publish",
            ),
        ],
    )
    def test_pipeline_outcome(
        self,
        monkeypatch,
        tmp_path,
        argv: list[str],
        codes: list[int],
        downloads_seed: tuple[str, ...] | None,
        expected_rc: int,
        expected_scripts: list[str],
        expected_last_args: tuple[str, ...] | None,
    ) -> None:
        rc, fake = self._invoke(argv, codes, monkeypatch, tmp_path, downloads_seed=downloads_seed)
        names = [c[0].name for c in fake.calls]
        assert (rc, names) == (expected_rc, expected_scripts)
        if expected_last_args is not None:
            r2_stage_args = [c[1] for c in fake.calls if c[0].name == _R2]
            assert expected_last_args in r2_stage_args, (
                f"expected {expected_last_args} in {r2_stage_args}"
            )
        if downloads_seed and _VERIFY_POS in expected_scripts:
            verify_args = fake.calls[expected_scripts.index(_VERIFY_POS)][1]
            assert verify_args[0] == "--positions"
            assert verify_args[1].endswith(downloads_seed[0])

    def test_success_writes_marker(self, monkeypatch, tmp_path):
        marker = tmp_path / ".last_run"
        assert not marker.exists()
        rc, _ = self._invoke(["--force"], [0, 0, 0, 0], monkeypatch, tmp_path)
        assert rc == 0
        assert marker.exists()
        assert marker.read_text().strip()

    def test_dry_run_does_not_write_marker(self, monkeypatch, tmp_path):
        marker = tmp_path / ".last_run"
        rc, _ = self._invoke(["--force", "--dry-run"], [0, 0, 0], monkeypatch, tmp_path)
        assert rc == EXIT_OK
        assert not marker.exists(), "dry-run wrote the marker; next real sync would be skipped"

    def test_no_changes_returns_0_without_invoking_build(self, monkeypatch, tmp_path):
        fake = _FakeRun([])
        monkeypatch.setattr(runner, "run_python_script", fake)
        _stub_runner_env(monkeypatch, tmp_path).write_text("seeded")
        db_path = _seed_computed_daily(tmp_path, date.today().isoformat())
        monkeypatch.setenv("PORTAL_DB_PATH", str(db_path))

        assert run_automation.main([]) == EXIT_OK
        assert fake.calls == []


# ── find_new_positions_csv() ──────────────────────────────────────────────────

class TestFindNewPositionsCSV:
    def test_returns_none_when_downloads_missing(self, tmp_path):
        assert changes.find_new_positions_csv(tmp_path / "nope", tmp_path / ".last_run") is None

    @pytest.mark.parametrize(
        ("files", "marker_age", "expected"),
        [
            pytest.param([("Accounts_History.csv", 0)], None, None, id="no-position-files"),
            pytest.param([("Portfolio_Positions_Apr-07-2026.csv", 0)], None, "Portfolio_Positions_Apr-07-2026.csv", id="marker-missing"),
            pytest.param([("Portfolio_Positions_Apr-07-2026.csv", 3600)], 0, None, id="older-than-marker"),
            pytest.param(
                [("Portfolio_Positions_Apr-03-2026.csv", 1800), ("Portfolio_Positions_Apr-07-2026.csv", 0)],
                7200,
                "Portfolio_Positions_Apr-07-2026.csv",
                id="newest-fresh",
            ),
        ],
    )
    def test_selects_fresh_positions_csv(
        self,
        tmp_path: Path,
        files: list[tuple[str, int]],
        marker_age: int | None,
        expected: str | None,
    ) -> None:
        downloads = tmp_path / "dl"
        downloads.mkdir()
        marker = tmp_path / ".last_run"
        if marker_age is not None:
            _write_file(marker, "fresh", age_seconds=marker_age)
        for filename, age in files:
            _write_file(downloads / filename, age_seconds=age)
        result = changes.find_new_positions_csv(downloads, marker)
        assert result == (downloads / expected if expected else None)


# ── Email notifications ───────────────────────────────────────────────────────


class TestEmailNotifications:
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
        outputs=None,
    ):
        fake = _FakeRun(codes, outputs)
        monkeypatch.setattr(runner, "run_python_script", fake)
        _stub_runner_env(
            monkeypatch,
            tmp_path,
            downloads_seed,
            email_enabled=not disable_email,
        )

        snapshots = [snapshot_before or SyncSnapshot()]
        if snapshot_after is not None:
            snapshots.append(snapshot_after)

        def fake_capture(_path):
            if len(snapshots) > 1:
                return snapshots.pop(0)
            return snapshots[0]

        monkeypatch.setattr(runner, "capture", fake_capture)

        sent_calls = []

        def fake_send(subject, html, text, config):
            sent_calls.append({"subject": subject, "html": html, "text": text, "config": config})
            if send_side_effect:
                raise send_side_effect

        monkeypatch.setattr(notify, "send", fake_send)

        rc = run_automation.main(argv)
        return rc, fake, sent_calls

    @pytest.mark.parametrize(
        ("before", "after", "expected_text"),
        [
            (SyncSnapshot(), SyncSnapshot(), None),
            (
                SyncSnapshot(net_worth=NetWorthPoint("2026-04-30", 1000)),
                SyncSnapshot(net_worth=NetWorthPoint("2026-05-01", 1100)),
                "+$100.00",
            ),
        ],
        ids=["no-diff", "net-worth-change"],
    )
    def test_success_email_summary(
        self,
        monkeypatch,
        tmp_path,
        before: SyncSnapshot,
        after: SyncSnapshot,
        expected_text: str | None,
    ):
        rc, _, sent = self._invoke_with_email(
            ["--force"], [0, 0, 0, 0], monkeypatch, tmp_path,
            snapshot_before=before, snapshot_after=after,
        )
        assert rc == EXIT_OK
        assert len(sent) == 1
        assert sent[0]["subject"].startswith("[Portal Sync] OK")
        if expected_text:
            assert expected_text in sent[0]["text"]

    @pytest.mark.parametrize(
        ("codes", "downloads_seed", "expected_rc", "expected_script", "expected_subject"),
        [
            ([5], None, EXIT_BUILD_FAIL, "build_timemachine_db.py", "BUILD FAILED"),
            ([0, 1], ("Portfolio_Positions_Apr-07-2026.csv",), EXIT_POSITIONS_FAIL, "verify_positions.py", None),
        ],
        ids=["build-failure", "positions-failure"],
    )
    def test_failure_email_includes_stage_and_duration(
        self,
        monkeypatch,
        tmp_path,
        codes: list[int],
        downloads_seed: tuple[str, ...] | None,
        expected_rc: int,
        expected_script: str,
        expected_subject: str | None,
    ):
        rc, _, sent = self._invoke_with_email(
            ["--force"], codes, monkeypatch, tmp_path,
            snapshot_before=SyncSnapshot(),
            snapshot_after=None,
            downloads_seed=downloads_seed,
        )
        assert rc == expected_rc
        assert len(sent) == 1
        assert "FAIL" in sent[0]["subject"]
        if expected_subject:
            assert expected_subject in sent[0]["subject"]
        assert expected_script in sent[0]["text"]
        assert "Duration:" in sent[0]["text"]

    def test_email_disabled_no_smtp_activity(self, monkeypatch, tmp_path, capsys):
        rc, _, sent = self._invoke_with_email(
            ["--force"], [0, 0, 0, 0], monkeypatch, tmp_path,
            snapshot_before=SyncSnapshot(),
            snapshot_after=SyncSnapshot(),
            disable_email=True,
        )
        assert rc == EXIT_OK
        assert sent == []
        captured = capsys.readouterr()
        assert "Email reporting: disabled" in captured.out

    def test_email_send_failure_does_not_fail_sync(self, monkeypatch, tmp_path, capsys):
        rc, _, sent = self._invoke_with_email(
            ["--force"], [0, 0, 0, 0], monkeypatch, tmp_path,
            snapshot_before=SyncSnapshot(),
            snapshot_after=SyncSnapshot(),
            send_side_effect=ConnectionRefusedError("smtp down"),
        )
        assert rc == EXIT_OK
        assert len(sent) == 1
        captured = capsys.readouterr()
        assert "Email send FAILED" in captured.out

    def test_success_email_includes_current_script_warnings(self, monkeypatch, tmp_path):
        rc, _, sent = self._invoke_with_email(
            ["--force"], [0, 0, 0, 0], monkeypatch, tmp_path,
            snapshot_before=SyncSnapshot(),
            snapshot_after=SyncSnapshot(),
            outputs=[["2026-04-12T12:00:01 WARNING: day_over_day 15.7% change"]],
        )
        assert rc == EXIT_OK
        assert "day_over_day 15.7% change" in sent[0]["text"]


# ── extract_validation_warnings() ─────────────────────────────────────────────


class TestExtractValidationWarnings:
    @pytest.mark.parametrize(
        ("lines", "expected_count", "expected_substrings"),
        [
            pytest.param(
                [
                    "2026-04-12 INFO [2] build",
                    "2026-04-12 WARNING: day_over_day 2023-07-04 -> 2023-07-05: 15.7% change",
                    "2026-04-12 INFO done",
                    "2026-04-12 WARNING date_gaps 8-day gap between dates",
                ],
                2, ["15.7%", "date_gaps"],
                id="parses-warning-lines",
            ),
            pytest.param(
                ["2026-04-12 WARNING: healthcheck ping failed (ignored): network down"],
                0, [],
                id="skips-healthcheck-failures",
            ),
            pytest.param(
                [
                    "2026-04-12T12:00:01 WARNING: day_over_day 2023-07-04 -> 2023-07-05: 15.7% change",
                    "2026-04-12T12:00:02 WARNING: day_over_day 2023-07-04 -> 2023-07-05: 15.7% change",
                    "2026-04-12T12:00:03 WARNING: day_over_day 2023-07-04 -> 2023-07-05: 15.7% change",
                ],
                1, ["15.7%"],
                id="dedup-exact-duplicates",
            ),
        ],
    )
    def test_filter_and_dedup(
        self, lines: list[str], expected_count: int, expected_substrings: list[str],
    ) -> None:
        warnings = notify.extract_validation_warnings(lines)
        assert len(warnings) == expected_count
        for i, sub in enumerate(expected_substrings):
            assert sub in warnings[i]

    def test_extract_warnings_buffer_scopes_to_current_main_run(
        self, monkeypatch, tmp_path, caplog
    ):
        monkeypatch.setattr(runner, "run_python_script", lambda script, *args: (0, []))
        _stub_runner_env(monkeypatch, tmp_path)

        monkeypatch.setattr(runner, "capture", lambda _p: SyncSnapshot())

        rc = run_automation.main(["--force"])
        assert rc == EXIT_OK
