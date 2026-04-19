"""Tests for incremental build and cross-check verification."""
from __future__ import annotations

from datetime import date

import pytest

from etl.db import (
    get_connection,
    get_last_computed_date,
    init_db,
    upsert_daily_rows,
)


@pytest.fixture()
def db(empty_db):
    return empty_db


def _insert_daily(db_path, rows):
    conn = get_connection(db_path)
    for r in rows:
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (r["date"], r["total"], r["us_equity"], r["non_us_equity"], r["crypto"], r["safe_net"]),
        )
    conn.commit()
    conn.close()


_DAY1 = {"date": "2025-01-02", "total": 100, "us_equity": 50, "non_us_equity": 20, "crypto": 10, "safe_net": 20}
_DAY2 = {"date": "2025-01-03", "total": 110, "us_equity": 55, "non_us_equity": 22, "crypto": 11, "safe_net": 22}


# ── get_last_computed_date ──────────────────────────────────────────────────


class TestGetLastComputedDate:
    def test_empty_db(self, db):
        assert get_last_computed_date(db) is None

    def test_returns_latest(self, db):
        _insert_daily(db, [_DAY1, _DAY2])
        assert get_last_computed_date(db) == date(2025, 1, 3)


# ── upsert_daily_rows ────────────────────────────────────────────────────────────


class TestUpsertDailyRows:
    def test_appends_new_rows(self, db):
        _insert_daily(db, [_DAY1])
        new = [{"date": "2025-01-03", "total": 110, "us_equity": 55,
                "non_us_equity": 22, "crypto": 11, "safe_net": 22,
                "liabilities": 0, "tickers": []}]
        assert upsert_daily_rows(db, new) == 1
        conn = get_connection(db)
        assert conn.execute("SELECT COUNT(*) FROM computed_daily").fetchone()[0] == 2
        conn.close()

    def test_upserts_existing_dates(self, db):
        """Recomputed row must replace the stored one (incremental refresh window)."""
        _insert_daily(db, [_DAY1])
        new = [
            {"date": "2025-01-02", "total": 999, "us_equity": 0,
             "non_us_equity": 0, "crypto": 0, "safe_net": 0,
             "liabilities": 0, "tickers": []},
            {"date": "2025-01-03", "total": 110, "us_equity": 55,
             "non_us_equity": 22, "crypto": 11, "safe_net": 22,
             "liabilities": 0, "tickers": []},
        ]
        assert upsert_daily_rows(db, new) == 2
        conn = get_connection(db)
        row = conn.execute("SELECT total FROM computed_daily WHERE date = '2025-01-02'").fetchone()
        conn.close()
        assert row[0] == 999  # overwritten with recomputed value

    def test_appends_tickers(self, db):
        new = [{"date": "2025-01-02", "total": 100, "us_equity": 50,
                "non_us_equity": 20, "crypto": 10, "safe_net": 20,
                "liabilities": 0,
                "tickers": [{"ticker": "VOO", "value": 50, "category": "US Equity",
                             "subtype": "S&P 500", "cost_basis": 40,
                             "gain_loss": 10, "gain_loss_pct": 25}]}]
        upsert_daily_rows(db, new)
        conn = get_connection(db)
        row = conn.execute("SELECT ticker, value FROM computed_daily_tickers WHERE date = '2025-01-02'").fetchone()
        conn.close()
        assert row == ("VOO", 50)

    def test_upsert_wipes_removed_tickers(self, db):
        """If a holding drops out on recompute, its ticker row must go too."""
        old = [{"date": "2025-01-02", "total": 100, "us_equity": 50,
                "non_us_equity": 20, "crypto": 10, "safe_net": 20,
                "liabilities": 0,
                "tickers": [
                    {"ticker": "VOO", "value": 50, "category": "US Equity",
                     "subtype": "S&P 500", "cost_basis": 40,
                     "gain_loss": 10, "gain_loss_pct": 25},
                    {"ticker": "OLD", "value": 25, "category": "US Equity",
                     "subtype": "Mid Cap", "cost_basis": 20,
                     "gain_loss": 5, "gain_loss_pct": 25},
                ]}]
        upsert_daily_rows(db, old)
        # Recompute without OLD
        new = [{"date": "2025-01-02", "total": 100, "us_equity": 50,
                "non_us_equity": 20, "crypto": 10, "safe_net": 20,
                "liabilities": 0,
                "tickers": [{"ticker": "VOO", "value": 75, "category": "US Equity",
                             "subtype": "S&P 500", "cost_basis": 40,
                             "gain_loss": 35, "gain_loss_pct": 87.5}]}]
        upsert_daily_rows(db, new)
        conn = get_connection(db)
        tickers = conn.execute(
            "SELECT ticker FROM computed_daily_tickers WHERE date = '2025-01-02'"
        ).fetchall()
        conn.close()
        assert {t[0] for t in tickers} == {"VOO"}  # OLD wiped

    def test_empty_input(self, db):
        assert upsert_daily_rows(db, []) == 0


# ── compute_inc_start ───────────────────────────────────────────────────────


class TestComputeIncStart:
    """The refresh-window range that `_build_refresh_window` hands to
    `compute_daily_allocation`. This is the decision that PR #156 fixed.
    """

    @staticmethod
    def _compute(last_iso: str, start_iso: str, end_iso: str) -> str:
        from scripts.build_timemachine_db import compute_inc_start
        return compute_inc_start(
            date.fromisoformat(last_iso),
            date.fromisoformat(start_iso),
            date.fromisoformat(end_iso),
        ).isoformat()

    def test_recomputes_full_window_when_last_is_recent(self):
        """last=yesterday, end=today → window covers REFRESH_WINDOW_DAYS back."""
        # end = 2026-04-14, refresh_window_start(end) = 2026-04-08
        assert self._compute("2026-04-13", "2023-01-01", "2026-04-14") == "2026-04-08"

    def test_fills_gap_when_last_is_far_back(self):
        """last far older than refresh window → range starts at last+1 (gap fill)."""
        # last = 30 days ago; should start at last+1, not refresh_floor
        assert self._compute("2026-03-15", "2023-01-01", "2026-04-14") == "2026-03-16"

    def test_clamps_to_configured_start(self):
        """Start clamps if last+1 and refresh_floor are both before it (unusual)."""
        # If caller passes a start later than the normal boundary, respect it.
        assert self._compute("2026-04-13", "2026-04-10", "2026-04-14") == "2026-04-10"

    def test_last_equals_end_still_refreshes_window(self):
        """Even if last == end, window must be recomputed (today's row moves)."""
        # PR #156 regression guard: before the fix, `last == end` silently skipped.
        result = self._compute("2026-04-14", "2023-01-01", "2026-04-14")
        assert result == "2026-04-08"  # 7-day window back from end

    def test_empty_window_when_end_before_start(self):
        """Returned date can exceed `end` — caller detects this as 'nothing to do'."""
        # last > end (call ordering quirk); inc_start = min(last+1, refresh_floor) = refresh_floor
        # refresh_floor = end - 6 days. So inc_start <= end always. Only start > end causes skip.
        from scripts.build_timemachine_db import compute_inc_start
        inc = compute_inc_start(
            date(2026, 4, 14), date(2026, 4, 20), date(2026, 4, 14),
        )
        assert inc > date(2026, 4, 14)


# ── _build_refresh_window orchestration (integration smoke) ────────────────


class TestBuildRefreshWindowOrchestration:
    """End-to-end wiring of `_build_refresh_window`.

    compute_daily_allocation has extensive unit tests. compute_inc_start has
    its own. This class covers the glue: does the wrapper read `last` from
    the DB, pass the correct (inc_start, end) downstream, and upsert results?
    """

    def _seeded_db(self, tmp_path, last_date: str):
        """Return a BuildPaths backed by a DB with one computed_daily row."""
        from scripts.build_timemachine_db import BuildPaths
        paths = BuildPaths(data_dir=tmp_path, config=tmp_path / "cfg.json",
                           downloads=tmp_path, csv=None)
        init_db(paths.db_path)  # property → tmp_path/timemachine.db
        conn = get_connection(paths.db_path)
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity,"
            " crypto, safe_net, liabilities) VALUES (?, 100000, 60000, 15000, 5000, 20000, 0)",
            (last_date,),
        )
        conn.commit()
        conn.close()
        return paths

    def test_passes_correct_range_to_compute(self, tmp_path, monkeypatch):
        """Wrapper reads last=2026-04-13, end=2026-04-14 → inc_start=2026-04-08."""
        from scripts import build_timemachine_db

        paths = self._seeded_db(tmp_path, "2026-04-13")

        captured: dict[str, object] = {}

        def _fake_compute(*args, **kwargs):
            captured["inc_start"] = args[3]  # positional: db_path, qj_db, config, start, end
            captured["end"] = args[4]
            return []

        monkeypatch.setattr(build_timemachine_db, "compute_daily_allocation", _fake_compute)
        monkeypatch.setattr(build_timemachine_db, "precompute_market", lambda _p: None)
        monkeypatch.setattr(build_timemachine_db, "precompute_holdings_detail", lambda _p: None)

        build_timemachine_db._build_refresh_window(
            paths, {}, date(2023, 1, 1), date(2026, 4, 14), no_validate=True,
        )
        assert captured["inc_start"] == date(2026, 4, 8)  # refresh_window_start(2026-04-14)
        assert captured["end"] == date(2026, 4, 14)

    def test_falls_back_to_full_build_when_db_empty(self, tmp_path, monkeypatch):
        """No rows in computed_daily → full rebuild from `start`."""
        from scripts import build_timemachine_db
        from scripts.build_timemachine_db import BuildPaths, _build_refresh_window

        paths = BuildPaths(data_dir=tmp_path, config=tmp_path / "cfg.json",
                           downloads=tmp_path, csv=None)
        init_db(paths.db_path)  # empty

        called = {"full_build": False}

        def _fake_full(*args, **kwargs):
            called["full_build"] = True
            return []

        monkeypatch.setattr(build_timemachine_db, "_full_build", _fake_full)
        _build_refresh_window(paths, {}, date(2023, 1, 1), date(2026, 4, 14))
        assert called["full_build"] is True

    def test_upserts_returned_rows(self, tmp_path, monkeypatch):
        """Rows returned by compute_daily_allocation land in computed_daily."""
        from scripts import build_timemachine_db

        paths = self._seeded_db(tmp_path, "2026-04-13")
        monkeypatch.setattr(build_timemachine_db, "compute_daily_allocation",
                            lambda *a, **kw: [{
                                "date": "2026-04-14", "total": 105000,
                                "us_equity": 62000, "non_us_equity": 15500,
                                "crypto": 5500, "safe_net": 22000, "liabilities": 0,
                                "tickers": [],
                            }])
        monkeypatch.setattr(build_timemachine_db, "precompute_market", lambda _p: None)
        monkeypatch.setattr(build_timemachine_db, "precompute_holdings_detail", lambda _p: None)

        build_timemachine_db._build_refresh_window(
            paths, {}, date(2023, 1, 1), date(2026, 4, 14), no_validate=True,
        )
        # The new row should be in the DB.
        conn = get_connection(paths.db_path)
        row = conn.execute(
            "SELECT total FROM computed_daily WHERE date = '2026-04-14'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 105000
