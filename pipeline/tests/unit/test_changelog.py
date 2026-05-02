"""Tests for etl/changelog.py — snapshot, diff, and email body formatting."""
from __future__ import annotations

import tempfile
from pathlib import Path

from etl.changelog import (
    NetWorthPoint,
    SyncChangelog,
    SyncSnapshot,
    build_subject,
    capture,
    diff,
    format_html,
    format_text,
)
from etl.db import init_db
from tests.fixtures import (
    connected_db,
    insert_close,
    insert_computed_daily,
    insert_fidelity_txn,
    insert_qianji_txn,
)


def _nwp(total: float, **components: float) -> NetWorthPoint:
    """Test helper: build a NetWorthPoint whose components sum to total.

    By default, total goes into us_equity (so ``component_sum == total``)
    and the email's drift flag won't fire on a plain snapshot. Callers that
    want to exercise the drift path pass explicit component values.
    """
    defaults = {
        "us_equity": total,
        "non_us_equity": 0.0,
        "crypto": 0.0,
        "safe_net": 0.0,
        "liabilities": 0.0,
    }
    defaults.update(components)
    return NetWorthPoint(total=total, **defaults)


def _make_db() -> Path:
    tmp = Path(tempfile.mktemp(suffix=".db"))
    init_db(tmp)
    return tmp


def _seed_fidelity(db_path: Path, rows: list[tuple[str, str, str, float, float]]) -> None:
    with connected_db(db_path) as conn:
        for run_date, action_type, symbol, qty, amount in rows:
            insert_fidelity_txn(
                conn, run_date=run_date, action="buy", action_type=action_type,
                symbol=symbol, quantity=qty, amount=amount,
            )


def _seed_qianji(db_path: Path, rows: list[tuple[str, str, str, float]]) -> None:
    with connected_db(db_path) as conn:
        for date, type_, category, amount in rows:
            insert_qianji_txn(conn, date=date, kind=type_, category=category, amount=amount)


def _seed_computed_daily(db_path: Path, rows: list[tuple[str, float]]) -> None:
    with connected_db(db_path) as conn:
        for date, total in rows:
            insert_computed_daily(conn, date, total)


def _seed_daily_close(db_path: Path, rows: list[tuple[str, str, float]]) -> None:
    with connected_db(db_path) as conn:
        for symbol, date, close in rows:
            insert_close(conn, symbol, date, close)


def _seed_econ(db_path: Path, rows: list[tuple[str, str, float]]) -> None:
    with connected_db(db_path) as conn:
        for key, date, value in rows:
            conn.execute(
                "INSERT INTO econ_series (key, date, value) VALUES (?, ?, ?)",
                (key, date, value),
            )


def _seed_empower(db_path: Path, dates: list[str]) -> None:
    with connected_db(db_path) as conn:
        for d in dates:
            conn.execute("INSERT INTO empower_snapshots (snapshot_date) VALUES (?)", (d,))


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
        assert ("2026-04-10", "expense", "Meals", 45.0, "") in snap.qianji_txns
        # computed_daily now stores NetWorthPoint per date (with component splits).
        assert set(snap.computed_daily.keys()) == {"2026-04-10", "2026-04-11"}
        assert snap.computed_daily["2026-04-10"].total == 100_000.0
        assert snap.computed_daily["2026-04-11"].total == 101_500.0
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
            ("2026-04-10", "expense", "Meals", 20.0, ""),
            ("2026-04-10", "expense", "Meals", 30.0, ""),
            ("2026-04-11", "expense", "Housing", 1500.0, "rent"),
        }))
        cl = diff(SyncSnapshot(), after)
        assert cl.qianji_added_count == 3
        assert cl.qianji_added_by_category["Meals"] == (2, 50.0)
        assert cl.qianji_added_by_category["Housing"] == (1, 1500.0)
        assert cl.qianji_added_rows_by_category["Housing"] == [("2026-04-11", 1500.0, "rent")]
        assert cl.has_meaningful_changes() is True

    def test_qianji_no_ghost_when_amount_stable(self) -> None:
        """Same bill in BEFORE and AFTER (identical tuple) — NOT counted as added.

        Regression guard: the CNY-live-rate drift that used to revalue
        historical bills every build (and inflate the Qianji diff as ghost
        additions) is now fixed at its root in ``parse_qj_amount`` via
        per-bill-date historical rates. Snapshots keyed on the content
        tuple work because the tuple stays stable.
        """
        shared = ("2024-05-18", "transfer", "", 1027.00, "")
        before = SyncSnapshot(qianji_txns=frozenset({shared}))
        after = SyncSnapshot(qianji_txns=frozenset({shared}))
        cl = diff(before, after)
        assert cl.qianji_added_count == 0
        assert cl.qianji_added_by_category == {}

    def test_computed_daily_new_dates(self) -> None:
        before = SyncSnapshot(computed_daily={"2026-04-10": _nwp(100.0)})
        after = SyncSnapshot(
            computed_daily={
                "2026-04-10": _nwp(100.0),
                "2026-04-11": _nwp(110.0),
                "2026-04-12": _nwp(120.0),
            },
        )
        cl = diff(before, after)
        # ``computed_daily_added`` stays float-keyed for back-compat — only
        # the ``total`` component makes it into the changelog delta.
        assert cl.computed_daily_added == {"2026-04-11": 110.0, "2026-04-12": 120.0}
        assert cl.has_meaningful_changes() is True

    def test_daily_close_delta_positive(self) -> None:
        before = SyncSnapshot(daily_close_count=500, daily_close_max_date="2026-04-10")
        after = SyncSnapshot(daily_close_count=582, daily_close_max_date="2026-04-12")
        cl = diff(before, after)
        assert cl.daily_close_added == 82
        assert cl.daily_close_max_after == "2026-04-12"
        assert cl.has_meaningful_changes() is True

    def test_daily_close_delta_never_negative(self) -> None:
        """If prod somehow shrinks (shouldn't happen), don't report negative."""
        before = SyncSnapshot(daily_close_count=600)
        after = SyncSnapshot(daily_close_count=500)
        cl = diff(before, after)
        assert cl.daily_close_added == 0

    def test_net_worth_delta_positive(self) -> None:
        before = SyncSnapshot(computed_daily={"2026-04-10": _nwp(100.0)})
        after = SyncSnapshot(
            computed_daily={"2026-04-10": _nwp(100.0), "2026-04-11": _nwp(200.0)},
        )
        cl = diff(before, after)
        assert cl.net_worth_before == 100.0
        assert cl.net_worth_after == 200.0
        assert cl.net_worth_delta == 100.0
        assert cl.net_worth_delta_pct() == 100.0

    def test_net_worth_delta_none_when_missing_before(self) -> None:
        after = SyncSnapshot(computed_daily={"2026-04-11": _nwp(200.0)})
        cl = diff(SyncSnapshot(), after)
        assert cl.net_worth_before is None
        assert cl.net_worth_delta is None
        assert cl.net_worth_delta_pct() is None

    def test_has_meaningful_changes_false_for_fred_only(self) -> None:
        """FRED with a stable key set is NOT meaningful — typical daily run."""
        keys = frozenset({"fedRate", "cpi"})
        before = SyncSnapshot(econ_series_keys=keys)
        after = SyncSnapshot(econ_series_keys=keys)
        cl = diff(before, after)
        # Same key set on both sides → refresh is considered a no-op.
        assert cl.econ_refreshed is False
        assert cl.has_meaningful_changes() is False

    def test_has_meaningful_changes_true_for_empower(self) -> None:
        after = SyncSnapshot(empower_snapshots_count=1)
        cl = diff(SyncSnapshot(), after)
        assert cl.empower_added == 1
        assert cl.has_meaningful_changes() is True

    # PR-S8 Bug 4 regression: FRED "refreshed" should fire only on key-set change
    def test_diff_econ_unchanged(self) -> None:
        """Same FRED key set before/after → econ_refreshed=False, no key lists."""
        keys = frozenset({"fedRate", "cpi", "unemployment"})
        cl = diff(
            SyncSnapshot(econ_series_keys=keys),
            SyncSnapshot(econ_series_keys=keys),
        )
        assert cl.econ_refreshed is False
        assert cl.econ_keys_added == []
        assert cl.econ_keys_removed == []

    def test_diff_econ_new_keys(self) -> None:
        """Added FRED indicator(s) → econ_refreshed=True, listed in econ_keys_added."""
        before = SyncSnapshot(econ_series_keys=frozenset({"fedRate", "cpi"}))
        after = SyncSnapshot(econ_series_keys=frozenset({"fedRate", "cpi", "pce", "spread3m10y"}))
        cl = diff(before, after)
        assert cl.econ_refreshed is True
        assert cl.econ_keys_added == ["pce", "spread3m10y"]
        assert cl.econ_keys_removed == []

    def test_diff_econ_removed_keys(self) -> None:
        """Removed FRED indicator(s) → econ_refreshed=True, listed in econ_keys_removed."""
        before = SyncSnapshot(econ_series_keys=frozenset({"fedRate", "cpi", "oil_wti"}))
        after = SyncSnapshot(econ_series_keys=frozenset({"fedRate", "cpi"}))
        cl = diff(before, after)
        assert cl.econ_refreshed is True
        assert cl.econ_keys_added == []
        assert cl.econ_keys_removed == ["oil_wti"]

    def test_diff_tracks_net_worth_dates(self) -> None:
        """Latest date for before/after is stored so formatter can render 'Unchanged'."""
        before = SyncSnapshot(computed_daily={"2026-04-10": _nwp(100.0)})
        after = SyncSnapshot(computed_daily={"2026-04-10": _nwp(100.0)})
        cl = diff(before, after)
        assert cl.net_worth_before_date == "2026-04-10"
        assert cl.net_worth_after_date == "2026-04-10"

    # Modify detection — PR-F2
    def test_qianji_note_edit_paired_as_modified(self) -> None:
        """Editing a note on an existing bill: pair via PK (date,type,category,amount)."""
        before = SyncSnapshot(qianji_txns=frozenset({
            ("2026-04-15", "expense", "Meals", 42.00, "lunch"),
        }))
        after = SyncSnapshot(qianji_txns=frozenset({
            ("2026-04-15", "expense", "Meals", 42.00, "lunch + coffee"),
        }))
        cl = diff(before, after)
        # Should NOT appear as added/removed — note edit is a modify.
        assert cl.qianji_added_count == 0
        assert cl.qianji_added_by_category == {}
        assert len(cl.qianji_modified) == 1
        before_row, after_row = cl.qianji_modified[0]
        assert before_row == ("2026-04-15", "expense", "Meals", 42.00, "lunch")
        assert after_row == ("2026-04-15", "expense", "Meals", 42.00, "lunch + coffee")
        # Modified rows ARE considered meaningful.
        assert cl.has_meaningful_changes() is True

    def test_qianji_added_and_modified_coexist(self) -> None:
        """Mixed diff: one genuinely new bill + one edited note must bucket correctly."""
        before = SyncSnapshot(qianji_txns=frozenset({
            ("2026-04-15", "expense", "Meals", 42.00, "lunch"),
        }))
        after = SyncSnapshot(qianji_txns=frozenset({
            ("2026-04-15", "expense", "Meals", 42.00, "lunch + coffee"),  # modified
            ("2026-04-16", "expense", "Meals", 18.50, "coffee"),           # genuinely new
        }))
        cl = diff(before, after)
        assert cl.qianji_added_count == 1  # just the 2026-04-16 one
        assert cl.qianji_added_by_category == {"Meals": (1, 18.50)}
        assert len(cl.qianji_modified) == 1
        assert cl.qianji_modified[0][1][0] == "2026-04-15"

    def test_qianji_amount_edit_still_counts_as_add_remove(self) -> None:
        """PK includes amount — so amount changes fall outside modify pairing.

        This is deliberate: amount changes are substantive and rare, best
        surfaced as separate (add, remove) than collapsed into a "modified"
        label that could hide a data-integrity issue.
        """
        before = SyncSnapshot(qianji_txns=frozenset({
            ("2026-04-15", "expense", "Meals", 42.00, "lunch"),
        }))
        after = SyncSnapshot(qianji_txns=frozenset({
            ("2026-04-15", "expense", "Meals", 45.00, "lunch"),
        }))
        cl = diff(before, after)
        assert cl.qianji_added_count == 1
        assert cl.qianji_modified == []

    def test_qianji_modified_sorted_by_date(self) -> None:
        """Modified rows sort by date (then type, category) for stable email output."""
        before = SyncSnapshot(qianji_txns=frozenset({
            ("2026-04-20", "expense", "Meals", 10.0, ""),
            ("2026-04-15", "expense", "Meals", 20.0, ""),
            ("2026-04-18", "expense", "Meals", 30.0, ""),
        }))
        after = SyncSnapshot(qianji_txns=frozenset({
            ("2026-04-20", "expense", "Meals", 10.0, "a"),
            ("2026-04-15", "expense", "Meals", 20.0, "b"),
            ("2026-04-18", "expense", "Meals", 30.0, "c"),
        }))
        cl = diff(before, after)
        dates = [pair[0][0] for pair in cl.qianji_modified]
        assert dates == ["2026-04-15", "2026-04-18", "2026-04-20"]

    def test_computed_daily_same_date_recompute_is_modified(self) -> None:
        """Same date, different NetWorthPoint → surfaces in computed_daily_modified.

        Regression: previously a same-date recompute (e.g. upstream price
        correction re-ran the allocation pipeline without advancing the
        date) was silently dropped because diff() only looked at
        ``dates not in before``.
        """
        before = SyncSnapshot(computed_daily={
            "2026-04-10": _nwp(100_000.0),
        })
        after = SyncSnapshot(computed_daily={
            "2026-04-10": _nwp(100_125.0),  # +$125 from a recompute
        })
        cl = diff(before, after)
        assert cl.computed_daily_added == {}  # not "added" — the date was there before
        assert "2026-04-10" in cl.computed_daily_modified
        b_pt, a_pt = cl.computed_daily_modified["2026-04-10"]
        assert b_pt.total == 100_000.0
        assert a_pt.total == 100_125.0
        assert cl.has_meaningful_changes() is True

    def test_computed_daily_unchanged_date_not_modified(self) -> None:
        """Identical NetWorthPoint on both sides is NOT reported as modified."""
        pt = _nwp(100_000.0)
        before = SyncSnapshot(computed_daily={"2026-04-10": pt})
        after = SyncSnapshot(computed_daily={"2026-04-10": pt})
        cl = diff(before, after)
        assert cl.computed_daily_modified == {}

    def test_computed_daily_new_date_not_modified(self) -> None:
        """A brand-new date belongs in 'added', never in 'modified'."""
        before = SyncSnapshot(computed_daily={"2026-04-10": _nwp(100.0)})
        after = SyncSnapshot(computed_daily={
            "2026-04-10": _nwp(100.0),
            "2026-04-11": _nwp(110.0),
        })
        cl = diff(before, after)
        assert cl.computed_daily_added == {"2026-04-11": 110.0}
        assert cl.computed_daily_modified == {}


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
        body = format_text(SyncChangelog(), _ctx())
        assert "Portal Sync Report" in body
        assert "2026-04-12 18:30" in body
        assert "Status: OK" in body

    def test_failure_shows_exit_code_and_error(self) -> None:
        body = format_text(
            SyncChangelog(),
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
        # Format D: sum first, then "(count record(s), avg $X)" — removes the
        # ambiguous "2 x $50.00" which read as "2 records of $50 each".
        assert "Meals: $50.00  (2 record(s), avg $25.00)" in body
        assert "Housing: $1,500.00  (1 record(s), avg $1,500.00)" in body

    def test_empower_section_shows_dollar_delta(self) -> None:
        """Snapshot value delta is attached to the +N line (was just count)."""
        cl = SyncChangelog(empower_added=1, empower_value_delta=2345.67)
        body = format_text(cl, _ctx())
        assert "Empower: +1 401k snapshot(s)" in body
        assert "+$2,345.67" in body

    def test_empower_section_opening_balance_when_no_prior(self) -> None:
        """First-ever snapshot: no delta available, show opening balance instead."""
        cl = SyncChangelog(
            empower_added=1,
            empower_value_after=12_000.0,
        )
        body = format_text(cl, _ctx())
        assert "opening balance" in body
        assert "$12,000.00" in body

    def test_duration_rendered_when_present(self) -> None:
        body = format_text(SyncChangelog(), _ctx(duration="41s"))
        assert "Duration: 41s" in body

    def test_duration_omitted_when_blank(self) -> None:
        body = format_text(SyncChangelog(), _ctx())  # no 'duration' key
        assert "Duration:" not in body

    def test_qianji_low_count_category_expands_detail(self) -> None:
        """For categories with count ≤ 2, render per-row date + note below the
        aggregate so one-offs like Salary/401k can be eyeballed without
        opening the DB."""
        cl = SyncChangelog(
            qianji_added_count=3,
            qianji_added_by_category={
                "Salary": (1, 5031.69),
                "Grocery": (5, 235.45),
            },
            qianji_added_rows_by_category={
                "Salary": [("2026-04-15", 5031.69, "ADP payroll")],
                "Grocery": [
                    ("2026-04-13", 45.50, ""),
                    ("2026-04-14", 60.00, ""),
                    ("2026-04-15", 40.00, ""),
                    ("2026-04-16", 55.00, ""),
                    ("2026-04-17", 34.95, ""),
                ],
            },
        )
        body = format_text(cl, _ctx())
        # Salary (count=1) expands with date + note.
        assert "2026-04-15" in body
        assert "ADP payroll" in body
        # Grocery (count=5) stays collapsed — no per-row dates in body.
        assert "2026-04-14" not in body
        assert "2026-04-16" not in body

    def test_empty_fidelity_and_qianji_dont_render_sections(self) -> None:
        body = format_text(SyncChangelog(), _ctx())
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
            SyncChangelog(),
            _ctx(warnings=["day_over_day 2023-07-04 -> 2023-07-05: 15.7% change"]),
        )
        assert "Warnings" in body
        assert "15.7%" in body

    def test_warnings_section_omitted_when_empty(self) -> None:
        body = format_text(SyncChangelog(), _ctx(warnings=[]))
        assert "Warnings" not in body

    def test_log_file_path_present(self) -> None:
        body = format_text(SyncChangelog(), _ctx(log_file="/tmp/sync.log"))
        assert "Log: /tmp/sync.log" in body

    def test_build_failed_case_no_snapshot_after(self) -> None:
        """exit_code != 0 with empty changelog (build crashed) must still render cleanly."""
        body = format_text(
            SyncChangelog(),
            _ctx(exit_code=1, status_label="BUILD FAILED", error="build_timemachine_db.py exited with code 1"),
        )
        # No net-worth block (both None), no rows, but still has Status + Error.
        assert "Net Worth" not in body
        assert "BUILD FAILED" in body

    # PR-S8 Bug 2 regression: Net Worth unchanged rendering
    def test_format_text_net_worth_unchanged(self) -> None:
        """Same date + zero delta → single 'Unchanged — DATE: $VALUE' line."""
        cl = SyncChangelog(
            net_worth_before=422386.32,
            net_worth_after=422386.32,
            net_worth_delta=0.0,
            net_worth_before_date="2026-04-10",
            net_worth_after_date="2026-04-10",
        )
        body = format_text(cl, _ctx())
        assert "Unchanged — 2026-04-10: $422,386.32" in body
        # Should NOT render the "before/after" two-line block or the delta.
        assert "+$0.00" not in body
        assert "+0.00%" not in body

    def test_format_text_net_worth_tiny_delta_still_treated_as_unchanged(self) -> None:
        """Delta < $0.01 + same date → collapse to 'Unchanged'."""
        cl = SyncChangelog(
            net_worth_before=100.0,
            net_worth_after=100.005,
            net_worth_delta=0.005,
            net_worth_before_date="2026-04-10",
            net_worth_after_date="2026-04-10",
        )
        body = format_text(cl, _ctx())
        assert "Unchanged — 2026-04-10" in body

    def test_format_text_net_worth_different_dates_shows_delta(self) -> None:
        """Different dates → render the before/after block even with zero delta."""
        cl = SyncChangelog(
            net_worth_before=100.0,
            net_worth_after=100.0,
            net_worth_delta=0.0,
            net_worth_before_date="2026-04-10",
            net_worth_after_date="2026-04-11",
        )
        body = format_text(cl, _ctx())
        assert "Unchanged" not in body
        # New layout: the date pair is its own line ("BEFORE → AFTER"), money
        # values live in a Total row beneath with delta/pct.
        assert "2026-04-10  →  2026-04-11" in body
        assert "Total:" in body
        assert "$100.00" in body
        assert "+0.00%" in body

    def test_format_text_net_worth_component_breakdown(self) -> None:
        """When point_before / point_after are populated, each component renders
        on its own row below the Total line."""
        before = NetWorthPoint(
            total=100_000.0, us_equity=60_000.0, non_us_equity=20_000.0,
            crypto=5_000.0, safe_net=15_000.0, liabilities=0.0,
        )
        after = NetWorthPoint(
            total=102_000.0, us_equity=61_000.0, non_us_equity=20_500.0,
            crypto=5_100.0, safe_net=15_500.0, liabilities=-100.0,
        )
        cl = SyncChangelog(
            net_worth_before=100_000.0, net_worth_after=102_000.0,
            net_worth_delta=2_000.0,
            net_worth_point_before=before, net_worth_point_after=after,
            net_worth_before_date="2026-04-10",
            net_worth_after_date="2026-04-11",
        )
        body = format_text(cl, _ctx())
        assert "Total:" in body
        assert "US Equity:" in body
        assert "Non-US:" in body
        assert "Crypto:" in body
        assert "Safe Net:" in body
        assert "Liabilities:" in body
        # Delta per component is sign-prefixed.
        assert "+$1,000.00" in body   # us_equity delta
        assert "+$500.00" in body     # non_us_equity delta
        assert "-$100.00" in body     # liabilities delta (liabilities went MORE negative)

    def test_format_text_net_worth_component_sum_drift_flagged(self) -> None:
        """When stored ``total`` and component sum disagree → ``[!]`` drift line."""
        after = NetWorthPoint(
            total=100_000.0,
            # Components only add up to 95,000 — 5k gap is "missing" from the
            # allocation pipeline. Email must surface this.
            us_equity=50_000.0, non_us_equity=20_000.0,
            crypto=5_000.0, safe_net=20_000.0, liabilities=0.0,
        )
        cl = SyncChangelog(
            net_worth_before=90_000.0, net_worth_after=100_000.0,
            net_worth_delta=10_000.0,
            net_worth_point_before=NetWorthPoint(
                total=90_000.0, us_equity=90_000.0, non_us_equity=0.0,
                crypto=0.0, safe_net=0.0, liabilities=0.0,
            ),
            net_worth_point_after=after,
            net_worth_before_date="2026-04-10",
            net_worth_after_date="2026-04-11",
        )
        body = format_text(cl, _ctx())
        assert "[!]" in body
        assert "don't sum to stored total" in body

    def test_format_text_net_worth_large_move_flag_by_usd(self) -> None:
        """|Δ| > $5,000 → LARGE MOVE flag on the header."""
        cl = SyncChangelog(
            net_worth_before=100_000.0, net_worth_after=106_000.0,
            net_worth_delta=6_000.0,
            net_worth_before_date="2026-04-10",
            net_worth_after_date="2026-04-11",
        )
        body = format_text(cl, _ctx())
        assert "LARGE MOVE" in body

    def test_format_text_net_worth_large_move_flag_by_pct(self) -> None:
        """|Δ%| > 3% → LARGE MOVE flag (dollar threshold NOT tripped)."""
        cl = SyncChangelog(
            net_worth_before=1_000.0, net_worth_after=1_050.0,  # only $50 but +5%
            net_worth_delta=50.0,
            net_worth_before_date="2026-04-10",
            net_worth_after_date="2026-04-11",
        )
        body = format_text(cl, _ctx())
        assert "LARGE MOVE" in body

    def test_format_text_net_worth_no_large_move_flag_on_ordinary_day(self) -> None:
        """Normal daily move (< $5k AND < 3%) → no flag."""
        cl = SyncChangelog(
            net_worth_before=100_000.0, net_worth_after=101_500.0,
            net_worth_delta=1_500.0,
            net_worth_before_date="2026-04-10",
            net_worth_after_date="2026-04-11",
        )
        body = format_text(cl, _ctx())
        assert "LARGE MOVE" not in body

    def test_format_text_net_worth_only_after_no_prior(self) -> None:
        """Only `after` value present → 'no prior snapshot' hint."""
        cl = SyncChangelog(
            net_worth_after=100.0,
            net_worth_after_date="2026-04-10",
        )
        body = format_text(cl, _ctx())
        assert "Net Worth" in body
        assert "2026-04-10: $100.00" in body
        assert "no prior snapshot" in body

    def test_format_text_no_blocked_at_on_success(self) -> None:
        """exit_code=0 → header has no ``Blocked at`` line; Changes carries counts."""
        cl = SyncChangelog(
            fidelity_added=[("2026-04-10", "buy", "VOO", 1.0, -500.0)],
            computed_daily_added={"2026-04-10": 100.0},
        )
        body = format_text(cl, _ctx(exit_code=0))
        assert "Blocked at" not in body
        assert "Fidelity: +1" in body

    def test_format_text_header_blocked_at_on_parity_failure(self) -> None:
        """exit_code=2 → header carries ``Blocked at: parity check (verify_vs_prod)``."""
        cl = SyncChangelog(
            fidelity_added=[("2026-04-10", "buy", "VOO", 1.0, -500.0)],
        )
        body = format_text(cl, _ctx(exit_code=2, status_label="PARITY GATE FAILED"))
        assert "Blocked at: parity check (verify_vs_prod)" in body
        # No separate D1 Sync section and no duplicated row counts.
        assert "D1 Sync" not in body
        assert "fidelity_transactions:" not in body

    def test_format_text_header_blocked_at_on_build_failure(self) -> None:
        body = format_text(SyncChangelog(), _ctx(exit_code=1, status_label="BUILD FAILED"))
        assert "Blocked at: build" in body

    def test_format_text_header_blocked_at_on_sync_failure(self) -> None:
        body = format_text(SyncChangelog(), _ctx(exit_code=3, status_label="SYNC FAILED"))
        assert "Blocked at: sync" in body

    def test_format_text_header_blocked_at_on_positions_failure(self) -> None:
        body = format_text(SyncChangelog(), _ctx(exit_code=4, status_label="POSITIONS GATE FAILED"))
        assert "Blocked at: positions check (verify_positions)" in body

    def test_format_text_header_blocked_at_on_parity_infra_failure(self) -> None:
        """exit_code=5 → header carries 'parity check (verify_vs_prod): infra error'."""
        body = format_text(
            SyncChangelog(),
            _ctx(exit_code=5, status_label="PARITY GATE COULD NOT RUN"),
        )
        assert "Blocked at: parity check (verify_vs_prod): infra error" in body
        assert "Status: PARITY GATE COULD NOT RUN" in body

    # PR-S8 Bug 4 regression: FRED line only renders when keys changed
    def test_format_text_fred_omitted_when_not_refreshed(self) -> None:
        """No key-set change → no FRED lines anywhere (Changes OR D1 Sync)."""
        cl = SyncChangelog(
            fidelity_added=[("2026-04-10", "buy", "VOO", 1.0, -500.0)],
            econ_refreshed=False,
        )
        body = format_text(cl, _ctx(econ_keys=["fedRate", "cpi", "pce"]))
        assert "FRED" not in body
        assert "econ_series" not in body

    def test_format_text_fred_added_keys_render(self) -> None:
        """New FRED indicator(s) → Changes section lists them."""
        cl = SyncChangelog(
            econ_refreshed=True,
            econ_keys_added=["pce", "spread3m10y"],
        )
        body = format_text(cl, _ctx())
        assert "FRED: +2 new indicator(s) (pce, spread3m10y)" in body

    def test_format_text_fred_removed_keys_render(self) -> None:
        """Removed FRED indicator(s) → Changes lists them."""
        cl = SyncChangelog(
            econ_refreshed=True,
            econ_keys_removed=["oil_wti"],
        )
        body = format_text(cl, _ctx())
        assert "FRED: -1 indicator(s) removed (oil_wti)" in body

    # Modified section — PR-F2
    def test_modified_section_omitted_when_empty(self) -> None:
        """No modified rows → section header absent from body."""
        body = format_text(SyncChangelog(), _ctx())
        assert "Modified" not in body

    def test_modified_section_shows_qianji_note_diff(self) -> None:
        """Edited note renders with before → after field diff."""
        cl = SyncChangelog(
            qianji_modified=[
                (
                    ("2026-04-15", "expense", "Meals", 42.00, "lunch"),
                    ("2026-04-15", "expense", "Meals", 42.00, "lunch + coffee"),
                ),
            ],
        )
        body = format_text(cl, _ctx())
        assert "Modified" in body
        assert "Qianji: 1 row(s)" in body
        assert "2026-04-15" in body
        assert "Meals" in body
        assert "$42.00" in body
        # Before/after note diff on its own line.
        assert '"lunch" → "lunch + coffee"' in body

    def test_modified_section_shows_computed_daily_component_diff(self) -> None:
        """Same-date recompute renders per-component delta for changed fields only."""
        before = NetWorthPoint(
            total=100_000.0, us_equity=60_000.0, non_us_equity=20_000.0,
            crypto=5_000.0, safe_net=15_000.0, liabilities=0.0,
        )
        after = NetWorthPoint(
            total=100_125.0, us_equity=60_125.0, non_us_equity=20_000.0,
            crypto=5_000.0, safe_net=15_000.0, liabilities=0.0,
        )
        cl = SyncChangelog(
            computed_daily_modified={"2026-04-10": (before, after)},
        )
        body = format_text(cl, _ctx())
        assert "Modified" in body
        assert "computed_daily: 1 row(s)" in body
        # Changed fields appear; unchanged ones (non_us_equity, crypto, ...)
        # must NOT pollute the output.
        assert "total:" in body
        assert "us_equity:" in body
        assert "+$125.00" in body
        assert "non_us_equity" not in body
        assert "crypto" not in body

    def test_modified_section_renders_both_tables(self) -> None:
        """Qianji + computed_daily modifications render together as separate bullets."""
        before_pt = NetWorthPoint(
            total=100.0, us_equity=100.0, non_us_equity=0.0,
            crypto=0.0, safe_net=0.0, liabilities=0.0,
        )
        after_pt = NetWorthPoint(
            total=110.0, us_equity=110.0, non_us_equity=0.0,
            crypto=0.0, safe_net=0.0, liabilities=0.0,
        )
        cl = SyncChangelog(
            qianji_modified=[
                (
                    ("2026-04-15", "expense", "Meals", 42.00, "a"),
                    ("2026-04-15", "expense", "Meals", 42.00, "b"),
                ),
            ],
            computed_daily_modified={"2026-04-10": (before_pt, after_pt)},
        )
        body = format_text(cl, _ctx())
        assert "Qianji: 1 row(s)" in body
        assert "computed_daily: 1 row(s)" in body

    def test_modified_appears_between_changes_and_net_worth(self) -> None:
        """Section ordering: Changes → Modified → Net Worth → Warnings."""
        cl = SyncChangelog(
            fidelity_added=[("2026-04-10", "buy", "VOO", 1.0, -500.0)],
            qianji_modified=[
                (
                    ("2026-04-15", "expense", "Meals", 42.00, "a"),
                    ("2026-04-15", "expense", "Meals", 42.00, "b"),
                ),
            ],
            net_worth_before=100.0, net_worth_after=150.0, net_worth_delta=50.0,
            net_worth_before_date="2026-04-09", net_worth_after_date="2026-04-10",
        )
        body = format_text(cl, _ctx())
        changes_idx = body.index("Changes")
        modified_idx = body.index("Modified")
        net_worth_idx = body.index("Net Worth")
        assert changes_idx < modified_idx < net_worth_idx


class TestFormatHtml:
    def test_wraps_text_in_pre_block(self) -> None:
        html = format_html(SyncChangelog(), _ctx())
        assert "<html>" in html
        assert "<pre" in html
        assert "Portal Sync" in html

    def test_html_escapes_special_chars(self) -> None:
        cl = SyncChangelog()
        html = format_html(cl, _ctx(exit_code=1, error="crash at <tag> & stuff"))
        assert "&lt;tag&gt;" in html
        assert "&amp; stuff" in html

    def test_success_uses_green_color(self) -> None:
        html = format_html(SyncChangelog(), _ctx(exit_code=0))
        assert "#2e7d32" in html

    def test_failure_uses_red_color(self) -> None:
        html = format_html(SyncChangelog(), _ctx(exit_code=1))
        assert "#c62828" in html

    def test_html_net_worth_unchanged_passthrough(self) -> None:
        """HTML body wraps the plain-text version → 'Unchanged' appears escaped."""
        cl = SyncChangelog(
            net_worth_before=100.0,
            net_worth_after=100.0,
            net_worth_delta=0.0,
            net_worth_before_date="2026-04-10",
            net_worth_after_date="2026-04-10",
        )
        html = format_html(cl, _ctx())
        # Em-dash is plain Unicode, not HTML-escaped.
        assert "Unchanged — 2026-04-10" in html

    def test_html_blocked_at_passthrough_on_failure(self) -> None:
        """HTML body reflects the header's ``Blocked at`` line on failure."""
        html = format_html(SyncChangelog(), _ctx(exit_code=2))
        assert "Blocked at: parity check (verify_vs_prod)" in html


# ── build_subject() ──────────────────────────────────────────────────────────


class TestBuildSubject:
    def test_failure_subject(self) -> None:
        subj = build_subject(SyncChangelog(), exit_code=1)
        assert "FAIL" in subj
        assert "BUILD FAILED" in subj

    def test_build_subject_includes_label_on_failure(self) -> None:
        """Failure subject should name the gate, not just the exit code."""
        cl = SyncChangelog()
        assert build_subject(cl, 0) == "[Portal Sync] OK"
        assert build_subject(cl, 1) == "[Portal Sync] FAIL — BUILD FAILED"
        assert build_subject(cl, 2) == "[Portal Sync] FAIL — PARITY GATE FAILED"
        assert build_subject(cl, 5) == "[Portal Sync] FAIL — PARITY GATE COULD NOT RUN"
        # Unknown code falls back to the exit number.
        assert build_subject(cl, 99) == "[Portal Sync] FAIL (exit 99)"

    def test_success_no_changes(self) -> None:
        subj = build_subject(SyncChangelog(), exit_code=0)
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
