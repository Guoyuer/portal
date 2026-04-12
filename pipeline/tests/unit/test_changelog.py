"""Tests for etl/changelog.py — snapshot, diff, and email body formatting."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from etl.changelog import (
    SyncChangelog,
    SyncSnapshot,
    build_subject,
    capture,
    diff,
    empty_changelog,
    format_html,
    format_text,
)
from etl.db import init_db


def _make_db() -> Path:
    tmp = Path(tempfile.mktemp(suffix=".db"))
    init_db(tmp)
    return tmp


def _seed_fidelity(db_path: Path, rows: list[tuple[str, str, str, float, float]]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        for run_date, action_type, symbol, qty, amount in rows:
            conn.execute(
                "INSERT INTO fidelity_transactions "
                "(run_date, account, account_number, action, action_type, symbol, quantity, amount) "
                "VALUES (?, 'X', 'Z', 'buy', ?, ?, ?, ?)",
                (run_date, action_type, symbol, qty, amount),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_qianji(db_path: Path, rows: list[tuple[str, str, str, float]]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        for date, type_, category, amount in rows:
            conn.execute(
                "INSERT INTO qianji_transactions (date, type, category, amount) "
                "VALUES (?, ?, ?, ?)",
                (date, type_, category, amount),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_computed_daily(db_path: Path, rows: list[tuple[str, float]]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        for date, total in rows:
            conn.execute(
                "INSERT INTO computed_daily "
                "(date, total, us_equity, non_us_equity, crypto, safe_net) "
                "VALUES (?, ?, 0, 0, 0, 0)",
                (date, total),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_daily_close(db_path: Path, rows: list[tuple[str, str, float]]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        for symbol, date, close in rows:
            conn.execute(
                "INSERT INTO daily_close (symbol, date, close) VALUES (?, ?, ?)",
                (symbol, date, close),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_econ(db_path: Path, rows: list[tuple[str, str, float]]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        for key, date, value in rows:
            conn.execute(
                "INSERT INTO econ_series (key, date, value) VALUES (?, ?, ?)",
                (key, date, value),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_empower(db_path: Path, dates: list[str]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        for d in dates:
            conn.execute(
                "INSERT INTO empower_snapshots (snapshot_date) VALUES (?)",
                (d,),
            )
        conn.commit()
    finally:
        conn.close()


# ── capture() ────────────────────────────────────────────────────────────────


class TestCapture:
    def test_missing_db_returns_empty_snapshot(self, tmp_path: Path) -> None:
        snap = capture(tmp_path / "does_not_exist.db")
        assert snap.fidelity_txns == frozenset()
        assert snap.computed_daily == {}
        assert snap.daily_close_count == 0
        assert snap.daily_close_max_date == ""
        assert snap.econ_series_keys == frozenset()
        assert snap.empower_snapshots_count == 0

    def test_round_trip_all_fields(self) -> None:
        db = _make_db()
        _seed_fidelity(db, [
            ("2026-04-10", "buy", "VOO", 1.0, -505.23),
            ("2026-04-11", "dividend", "SCHD", 0.0, 12.50),
        ])
        _seed_qianji(db, [
            ("2026-04-10", "expense", "Meals", 45.00),
            ("2026-04-11", "income", "Salary", 5000.00),
        ])
        _seed_computed_daily(db, [("2026-04-10", 100_000.0), ("2026-04-11", 101_500.0)])
        _seed_daily_close(db, [
            ("VOO", "2026-04-10", 500.0),
            ("VOO", "2026-04-11", 505.0),
            ("CNY=X", "2026-04-11", 7.20),
        ])
        _seed_econ(db, [("fedRate", "2026-04-01", 4.25), ("cpi", "2026-03-01", 3.1)])
        _seed_empower(db, ["2026-03-31"])

        snap = capture(db)
        assert ("2026-04-10", "buy", "VOO", 1.0, -505.23) in snap.fidelity_txns
        assert len(snap.fidelity_txns) == 2
        assert ("2026-04-10", "expense", "Meals", 45.0) in snap.qianji_txns
        assert snap.computed_daily == {"2026-04-10": 100_000.0, "2026-04-11": 101_500.0}
        assert snap.daily_close_count == 3
        assert snap.daily_close_max_date == "2026-04-11"
        assert snap.econ_series_keys == frozenset({"fedRate", "cpi"})
        assert snap.empower_snapshots_count == 1


# ── diff() ───────────────────────────────────────────────────────────────────


class TestDiff:
    def test_empty_to_empty_yields_empty_changelog(self) -> None:
        cl = diff(SyncSnapshot(), SyncSnapshot())
        assert cl.fidelity_added == []
        assert cl.qianji_added_count == 0
        assert cl.computed_daily_added == {}
        assert cl.daily_close_added == 0
        assert cl.empower_added == 0
        assert cl.net_worth_before is None
        assert cl.net_worth_after is None
        assert cl.net_worth_delta is None
        assert cl.has_meaningful_changes() is False

    def test_fidelity_added_is_sorted_by_date(self) -> None:
        before = SyncSnapshot()
        after = SyncSnapshot(fidelity_txns=frozenset({
            ("2026-04-12", "buy", "VOO", 1.0, -500.0),
            ("2026-04-10", "buy", "VTI", 2.0, -200.0),
            ("2026-04-11", "dividend", "SCHD", 0.0, 15.0),
        }))
        cl = diff(before, after)
        dates = [row[0] for row in cl.fidelity_added]
        assert dates == ["2026-04-10", "2026-04-11", "2026-04-12"]
        assert cl.has_meaningful_changes() is True

    def test_qianji_tallies_by_category(self) -> None:
        after = SyncSnapshot(qianji_txns=frozenset({
            ("2026-04-10", "expense", "Meals", 20.0),
            ("2026-04-10", "expense", "Meals", 30.0),
            ("2026-04-11", "expense", "Housing", 1500.0),
        }))
        cl = diff(SyncSnapshot(), after)
        assert cl.qianji_added_count == 3
        assert cl.qianji_added_by_category["Meals"] == (2, 50.0)
        assert cl.qianji_added_by_category["Housing"] == (1, 1500.0)
        assert cl.has_meaningful_changes() is True

    def test_computed_daily_new_dates(self) -> None:
        before = SyncSnapshot(computed_daily={"2026-04-10": 100.0})
        after = SyncSnapshot(
            computed_daily={"2026-04-10": 100.0, "2026-04-11": 110.0, "2026-04-12": 120.0},
        )
        cl = diff(before, after)
        assert cl.computed_daily_added == {"2026-04-11": 110.0, "2026-04-12": 120.0}
        assert cl.has_meaningful_changes() is True

    def test_daily_close_delta_positive(self) -> None:
        before = SyncSnapshot(daily_close_count=500, daily_close_max_date="2026-04-10")
        after = SyncSnapshot(daily_close_count=582, daily_close_max_date="2026-04-12")
        cl = diff(before, after)
        assert cl.daily_close_added == 82
        assert cl.daily_close_max_before == "2026-04-10"
        assert cl.daily_close_max_after == "2026-04-12"
        assert cl.has_meaningful_changes() is True

    def test_daily_close_delta_never_negative(self) -> None:
        """If prod somehow shrinks (shouldn't happen), don't report negative."""
        before = SyncSnapshot(daily_close_count=600)
        after = SyncSnapshot(daily_close_count=500)
        cl = diff(before, after)
        assert cl.daily_close_added == 0

    def test_net_worth_delta_positive(self) -> None:
        before = SyncSnapshot(computed_daily={"2026-04-10": 100.0})
        after = SyncSnapshot(computed_daily={"2026-04-10": 100.0, "2026-04-11": 200.0})
        cl = diff(before, after)
        assert cl.net_worth_before == 100.0
        assert cl.net_worth_after == 200.0
        assert cl.net_worth_delta == 100.0
        assert cl.net_worth_delta_pct() == 100.0

    def test_net_worth_delta_none_when_missing_before(self) -> None:
        after = SyncSnapshot(computed_daily={"2026-04-11": 200.0})
        cl = diff(SyncSnapshot(), after)
        assert cl.net_worth_before is None
        assert cl.net_worth_delta is None
        assert cl.net_worth_delta_pct() is None

    def test_has_meaningful_changes_false_for_fred_only(self) -> None:
        """FRED refresh alone is not meaningful — it happens every run."""
        before = SyncSnapshot()
        after = SyncSnapshot(econ_series_keys=frozenset({"fedRate", "cpi"}))
        cl = diff(before, after)
        assert cl.econ_refreshed is True
        assert cl.has_meaningful_changes() is False

    def test_has_meaningful_changes_true_for_empower(self) -> None:
        after = SyncSnapshot(empower_snapshots_count=1)
        cl = diff(SyncSnapshot(), after)
        assert cl.empower_added == 1
        assert cl.has_meaningful_changes() is True


# ── format_text() / format_html() ────────────────────────────────────────────


def _ctx(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "timestamp": "2026-04-12 18:30",
        "status_label": "OK",
        "exit_code": 0,
        "log_file": r"C:\Users\me\logs\sync-2026-04-12.log",
        "error": None,
        "warnings": [],
        "before_dates": ["2026-04-11"],
        "after_dates": ["2026-04-12"],
        "econ_keys": ["fedRate", "cpi"],
    }
    base.update(overrides)
    return base


class TestFormatText:
    def test_header_contains_timestamp_and_status(self) -> None:
        body = format_text(empty_changelog(), _ctx())
        assert "Portal Sync Report" in body
        assert "2026-04-12 18:30" in body
        assert "Status: OK" in body

    def test_failure_shows_exit_code_and_error(self) -> None:
        body = format_text(
            empty_changelog(),
            _ctx(exit_code=1, status_label="BUILD FAILED", error="build_timemachine_db.py exited with code 1"),
        )
        assert "Exit code: 1" in body
        assert "build_timemachine_db.py exited with code 1" in body

    def test_fidelity_section_lists_rows(self) -> None:
        cl = SyncChangelog(
            fidelity_added=[("2026-04-10", "buy", "VOO", 1.0, -505.23)],
        )
        body = format_text(cl, _ctx())
        assert "Fidelity: +1 transaction" in body
        assert "2026-04-10" in body
        assert "VOO" in body
        assert "-$505.23" in body

    def test_qianji_section_lists_categories(self) -> None:
        cl = SyncChangelog(
            qianji_added_count=3,
            qianji_added_by_category={"Meals": (2, 50.0), "Housing": (1, 1500.0)},
        )
        body = format_text(cl, _ctx())
        assert "Qianji: +3 record" in body
        assert "Meals: 2" in body
        assert "Housing: 1" in body

    def test_empty_fidelity_and_qianji_dont_render_sections(self) -> None:
        body = format_text(empty_changelog(), _ctx())
        assert "Fidelity:" not in body
        assert "Qianji:" not in body
        # Should still emit the "(no changes detected)" placeholder
        assert "no changes detected" in body.lower()

    def test_net_worth_block(self) -> None:
        cl = SyncChangelog(
            net_worth_before=100.0, net_worth_after=200.0, net_worth_delta=100.0,
        )
        body = format_text(cl, _ctx())
        assert "Net Worth" in body
        assert "$100.00" in body
        assert "$200.00" in body
        assert "+$100.00" in body
        assert "+100.00%" in body

    def test_warnings_rendered_when_present(self) -> None:
        body = format_text(
            empty_changelog(),
            _ctx(warnings=["day_over_day 2023-07-04 -> 2023-07-05: 15.7% change"]),
        )
        assert "Warnings" in body
        assert "15.7%" in body

    def test_warnings_section_omitted_when_empty(self) -> None:
        body = format_text(empty_changelog(), _ctx(warnings=[]))
        assert "Warnings" not in body

    def test_log_file_path_present(self) -> None:
        body = format_text(empty_changelog(), _ctx(log_file="/tmp/sync.log"))
        assert "Log: /tmp/sync.log" in body

    def test_build_failed_case_no_snapshot_after(self) -> None:
        """exit_code != 0 with empty changelog (build crashed) must still render cleanly."""
        body = format_text(
            empty_changelog(),
            _ctx(exit_code=1, status_label="BUILD FAILED", error="build_timemachine_db.py exited with code 1"),
        )
        # No net-worth block (both None), no rows, but still has Status + Error.
        assert "Net Worth" not in body
        assert "BUILD FAILED" in body


class TestFormatHtml:
    def test_wraps_text_in_pre_block(self) -> None:
        html = format_html(empty_changelog(), _ctx())
        assert "<html>" in html
        assert "<pre" in html
        assert "Portal Sync" in html

    def test_html_escapes_special_chars(self) -> None:
        cl = SyncChangelog()
        html = format_html(cl, _ctx(exit_code=1, error="crash at <tag> & stuff"))
        assert "&lt;tag&gt;" in html
        assert "&amp; stuff" in html

    def test_success_uses_green_color(self) -> None:
        html = format_html(empty_changelog(), _ctx(exit_code=0))
        assert "#2e7d32" in html

    def test_failure_uses_red_color(self) -> None:
        html = format_html(empty_changelog(), _ctx(exit_code=1))
        assert "#c62828" in html


# ── build_subject() ──────────────────────────────────────────────────────────


class TestBuildSubject:
    def test_failure_subject(self) -> None:
        subj = build_subject(empty_changelog(), exit_code=1)
        assert "FAIL" in subj
        assert "1" in subj

    def test_success_no_changes(self) -> None:
        subj = build_subject(empty_changelog(), exit_code=0)
        assert subj == "[Portal Sync] OK"

    def test_success_with_changes(self) -> None:
        cl = SyncChangelog(
            fidelity_added=[("2026-04-10", "buy", "VOO", 1.0, -500.0)],
            qianji_added_count=5,
            net_worth_delta=1000.0,
        )
        subj = build_subject(cl, exit_code=0)
        assert "1 fidelity" in subj
        assert "5 qianji" in subj
        assert "nw +$1,000.00" in subj
