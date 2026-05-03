"""Focused tests for timemachine build orchestration helpers."""
from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from etl import build as build_mod
from etl.db import init_db
from etl.sources.empower import Contribution
from etl.validate import CheckResult, Severity
from tests.fixtures import connected_db, db_rows, db_value


@pytest.fixture()
def paths(tmp_path: Path) -> build_mod.BuildPaths:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    return build_mod.BuildPaths(
        data_dir=tmp_path,
        config=tmp_path / "config.json",
        downloads=downloads,
    )


def _alloc_row(day: str = "2026-04-14", total: float = 100.0) -> build_mod.AllocationRow:
    return {
        "date": day,
        "total": total,
        "us_equity": total, "non_us_equity": 0, "crypto": 0, "safe_net": 0, "liabilities": 0,
        "tickers": [
            {
                "ticker": "VOO", "value": total, "category": "US Equity", "subtype": "broad",
                "cost_basis": total - 10, "gain_loss": 10, "gain_loss_pct": 10,
            }
        ],
    }


def _exec(db_path: Path, *sqls: str) -> None:
    with connected_db(db_path) as conn:
        for sql in sqls:
            conn.execute(sql)


def _seed_computed(db_path: Path, day: str = "2026-04-14", total: float = 1) -> None:
    _exec(
        db_path,
        "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
        f" VALUES ('{day}', {total}, {total}, 0, 0, 0)",
    )


def _seed_fidelity(db_path: Path, day: str, action_type: str = "buy", symbol: str = "VOO") -> None:
    _exec(
        db_path,
        "INSERT INTO fidelity_transactions (run_date, action, action_type, symbol)"
        f" VALUES ('{day}', '{action_type.title()}', '{action_type}', '{symbol}')",
    )


def _stub_full_build_inputs(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[tuple[str, object]] | None = None,
    *,
    allocation: list[build_mod.AllocationRow] | None = None,
    qianji_records: list[dict[str, object]] | None = None,
) -> None:
    monkeypatch.setattr(build_mod, "compute_daily_allocation", lambda *args: allocation or [])
    monkeypatch.setattr(build_mod, "load_cny_rates", lambda db: {"2026-04-14": 7.2})
    monkeypatch.setattr(build_mod, "load_all_from_db", lambda qj, historical_cny_rates: qianji_records or [])

    def fake_ingest_qianji(db_path, records, *, retirement_categories):
        if calls is not None:
            calls.append(("qianji", (records, retirement_categories)))
        return len(records)

    monkeypatch.setattr(build_mod, "ingest_qianji_transactions", fake_ingest_qianji)


class TestLoadPricesFromCsv:
    def test_loads_valid_prices_and_skips_blank_or_invalid_cells(self, paths: build_mod.BuildPaths) -> None:
        init_db(paths.db_path)
        price_csv = paths.data_dir / "prices.csv"
        price_csv.write_text(
            "date,VOO,VXUS\n"
            "2026-01-01,100.5,\n"
            "2026-01-02,bad,55\n"
            ",101,56\n",
            encoding="utf-8",
        )

        build_mod._load_prices_from_csv(paths.db_path, price_csv)

        assert db_rows(
            paths.db_path,
            "SELECT symbol, date, close FROM daily_close ORDER BY symbol, date"
        ) == [("VOO", "2026-01-01", 100.5), ("VXUS", "2026-01-02", 55.0)]

    def test_empty_csv_is_noop(self, paths: build_mod.BuildPaths) -> None:
        init_db(paths.db_path)
        price_csv = paths.data_dir / "prices.csv"
        price_csv.write_text("", encoding="utf-8")

        build_mod._load_prices_from_csv(paths.db_path, price_csv)

        assert db_value(paths.db_path, "SELECT COUNT(*) FROM daily_close") == 0


class TestConfigAndFidelityIngest:
    def test_load_config_accepts_object_root(self, tmp_path: Path) -> None:
        config = tmp_path / "config.json"
        config.write_text('{"goal": 1000}', encoding="utf-8")
        assert build_mod._load_config(config)["goal"] == 1000

    def test_load_config_rejects_non_object_root(self, tmp_path: Path) -> None:
        config = tmp_path / "config.json"
        config.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ValueError, match="Config root must be an object"):
            build_mod._load_config(config)

    def test_directory_ingest_reports_persisted_row_count(
        self,
        paths: build_mod.BuildPaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        init_db(paths.db_path)

        def fake_ingest(db_path: Path, config: dict[str, Path]) -> None:
            assert config == {"fidelity_downloads": paths.downloads}
            _seed_fidelity(db_path, "2026-01-01")

        monkeypatch.setattr(build_mod.fidelity_src, "ingest", fake_ingest)

        build_mod._ingest_fidelity_csvs(paths)

        assert db_value(paths.db_path, "SELECT COUNT(*) FROM fidelity_transactions") == 1


class TestQianjiFallbackAndValidation:
    def test_qianji_401k_fallback_returns_split_contribs_after_last_qfx(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        qj_db = tmp_path / "qianji.sqlite"
        before = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
        after = int(datetime(2026, 2, 1, tzinfo=UTC).timestamp())
        with closing(sqlite3.connect(qj_db)) as conn:
            conn.execute(
                "CREATE TABLE user_bill (money REAL, time INTEGER, status INTEGER, type INTEGER, fromact TEXT)"
            )
            conn.execute("INSERT INTO user_bill VALUES (200, ?, 1, 1, '401k')", (before,))
            conn.execute("INSERT INTO user_bill VALUES (300, ?, 1, 1, '401k')", (after,))
            conn.execute("INSERT INTO user_bill VALUES (999, ?, 0, 1, '401k')", (after,))
            conn.commit()
        monkeypatch.setattr(build_mod, "DEFAULT_QJ_DB", qj_db)

        contribs = build_mod._qianji_401k_fallback_contribs(date(2026, 1, 15))

        assert contribs == [
            Contribution(date=date(2026, 2, 1), amount=150.0, ticker="401k sp500"),
            Contribution(date=date(2026, 2, 1), amount=150.0, ticker="401k ex-us"),
        ]

    def test_qianji_401k_fallback_without_snapshot_or_db_is_empty(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(build_mod, "DEFAULT_QJ_DB", tmp_path / "missing.sqlite")
        assert build_mod._qianji_401k_fallback_contribs(None) == []
        assert build_mod._qianji_401k_fallback_contribs(date(2026, 1, 1)) == []

    @pytest.mark.parametrize(
        ("results", "expected_exit"),
        [
            ([CheckResult("bad", Severity.FATAL, "broken")], 1),
            ([CheckResult("warn", Severity.WARNING, "heads up")], None),
        ],
    )
    def test_run_validation(
        self,
        paths: build_mod.BuildPaths,
        monkeypatch: pytest.MonkeyPatch,
        results: list[CheckResult],
        expected_exit: int | None,
    ) -> None:
        monkeypatch.setattr(
            build_mod,
            "validate_build",
            lambda _db: results,
        )

        if expected_exit is None:
            build_mod._run_validation(paths)
        else:
            with pytest.raises(SystemExit) as exc:
                build_mod._run_validation(paths)
            assert exc.value.code == expected_exit


class TestSourceIngestAndPeriods:
    def test_derive_start_date_uses_earliest_fidelity_run_date(self, paths: build_mod.BuildPaths) -> None:
        init_db(paths.db_path)
        _seed_fidelity(paths.db_path, "2026-03-01")
        _seed_fidelity(paths.db_path, "2026-01-01", symbol="VXUS")

        assert build_mod._derive_start_date(paths, fallback=date(2026, 5, 1)) == date(2026, 1, 1)

    def test_derive_start_date_falls_back_when_no_fidelity_rows(self, paths: build_mod.BuildPaths) -> None:
        init_db(paths.db_path)
        assert build_mod._derive_start_date(paths, fallback=date(2026, 5, 1)) == date(2026, 5, 1)

    def test_init_db_and_ingest_sources_wires_all_source_modules(
        self,
        paths: build_mod.BuildPaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[tuple[str, object]] = []

        monkeypatch.setattr(build_mod, "ingest_categories", lambda db, cfg: calls.append(("categories", cfg)))
        monkeypatch.setattr(build_mod, "_ingest_fidelity_csvs", lambda p: calls.append(("fidelity", p.db_path)))
        monkeypatch.setattr(
            build_mod.robinhood_src,
            "ingest",
            lambda db, cfg: calls.append(("robinhood", cfg["robinhood_downloads"])),
        )

        def fake_empower_ingest(db_path: Path, config: dict[str, Path]) -> None:
            calls.append(("empower", config["empower_downloads"]))
            _exec(db_path, "INSERT INTO empower_snapshots (snapshot_date) VALUES ('2026-01-31')")

        monkeypatch.setattr(build_mod.empower_src, "ingest", fake_empower_ingest)

        def fake_fallback(last_qfx_date: date | None) -> list[Contribution]:
            calls.append(("fallback-last", last_qfx_date))
            return [Contribution(date=date(2026, 2, 1), amount=100.0, ticker="401k sp500")]

        monkeypatch.setattr(build_mod, "_qianji_401k_fallback_contribs", fake_fallback)
        monkeypatch.setattr(
            build_mod.empower_src,
            "ingest_contributions",
            lambda db, rows: calls.append(("fallback-rows", rows)),
        )

        build_mod._init_db_and_ingest_sources(paths, {"goal": 1})

        assert calls[0] == ("categories", {"goal": 1})
        assert ("fidelity", paths.db_path) in calls
        assert ("robinhood", paths.downloads) in calls
        assert ("empower", paths.downloads) in calls
        assert ("fallback-last", date(2026, 1, 31)) in calls
        assert calls[-1][0] == "fallback-rows"

    def test_snapshot_date_helpers_return_min_and_max(self, paths: build_mod.BuildPaths) -> None:
        init_db(paths.db_path)
        assert build_mod._first_empower_snapshot_date(paths.db_path) is None
        assert build_mod._last_empower_snapshot_date(paths.db_path) is None
        _exec(
            paths.db_path,
            "INSERT INTO empower_snapshots (snapshot_date) VALUES ('2026-03-31')",
            "INSERT INTO empower_snapshots (snapshot_date) VALUES ('2026-01-31')",
        )

        assert build_mod._first_empower_snapshot_date(paths.db_path) == date(2026, 1, 31)
        assert build_mod._last_empower_snapshot_date(paths.db_path) == date(2026, 3, 31)

    def test_compute_holding_periods_unions_fidelity_proxies_market_and_robinhood(
        self,
        paths: build_mod.BuildPaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        init_db(paths.db_path)
        _exec(
            paths.db_path,
            "INSERT INTO empower_snapshots (snapshot_date) VALUES ('2026-01-01')",
            "INSERT INTO robinhood_transactions (txn_date, action_kind, ticker)"
            " VALUES ('2026-01-02', 'buy', 'HOOD')",
        )
        monkeypatch.setattr(
            build_mod,
            "symbol_holding_periods_from_db",
            lambda _db: {"VOO": (date(2026, 1, 5), None), "AAPL": (date(2026, 1, 10), None)},
        )

        periods, earliest = build_mod._compute_holding_periods(paths, date(2026, 4, 1))

        assert earliest == date(2026, 1, 5)
        assert periods["VOO"] == (date(2026, 1, 1), None)
        assert periods["QQQM"] == (date(2026, 1, 1), None)
        assert periods["VXUS"] == (date(2026, 1, 1), None)
        assert periods["HOOD"] == (date(2026, 1, 5), None)
        assert periods["^GSPC"] == (date(2026, 1, 5), None)


class TestFetchAndBuildOrchestration:
    def test_fetch_all_prices_uses_csv_loader_when_supplied(
        self,
        paths: build_mod.BuildPaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        loaded: dict[str, Path] = {}
        price_csv = paths.data_dir / "prices.csv"

        def fake_load(db_path: Path, csv_path: Path) -> None:
            loaded["db"] = db_path
            loaded["csv"] = csv_path

        monkeypatch.setattr(build_mod, "_load_prices_from_csv", fake_load)

        build_mod._fetch_all_prices(
            paths,
            {"VOO": (date(2026, 1, 1), None)},
            date(2026, 1, 1),
            date(2026, 4, 1),
            prices_from_csv=price_csv,
        )

        assert loaded == {"db": paths.db_path, "csv": price_csv}

    def test_fetch_all_prices_derives_global_and_cny_starts(
        self,
        paths: build_mod.BuildPaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        init_db(paths.db_path)
        _seed_computed(paths.db_path, "2026-01-15")
        _seed_fidelity(paths.db_path, "2026-01-03", "deposit", "")
        calls: dict[str, object] = {}

        def fake_prices(db_path: Path, periods: dict[str, tuple[date, date | None]], end: date, *, global_start: date) -> None:
            calls["prices"] = (db_path, periods, end, global_start)

        def fake_cny(db_path: Path, start: date, end: date) -> None:
            calls["cny"] = (db_path, start, end)

        monkeypatch.setattr(build_mod, "fetch_and_store_prices", fake_prices)
        monkeypatch.setattr(build_mod, "fetch_and_store_cny_rates", fake_cny)

        periods = {"VOO": (date(2026, 1, 10), None)}
        build_mod._fetch_all_prices(paths, periods, date(2026, 1, 10), date(2026, 4, 1))

        assert calls["prices"] == (paths.db_path, periods, date(2026, 4, 1), date(2026, 1, 15))
        assert calls["cny"] == (paths.db_path, date(2026, 1, 3), date(2026, 4, 1))

    def test_ingest_and_fetch_threads_periods_to_price_fetch(
        self,
        paths: build_mod.BuildPaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: dict[str, object] = {}
        monkeypatch.setattr(build_mod, "_init_db_and_ingest_sources", lambda p, c: calls.setdefault("ingest", (p, c)))
        monkeypatch.setattr(
            build_mod,
            "_compute_holding_periods",
            lambda p, end: ({"VOO": (date(2026, 1, 1), None)}, date(2026, 1, 1)),
        )

        def fake_fetch(paths_arg, periods, earliest, end, *, prices_from_csv=None):
            calls["fetch"] = (paths_arg, periods, earliest, end, prices_from_csv)

        monkeypatch.setattr(build_mod, "_fetch_all_prices", fake_fetch)
        price_csv = paths.data_dir / "prices.csv"

        build_mod._ingest_and_fetch(paths, {"goal": 1}, date(2026, 4, 1), prices_from_csv=price_csv)

        assert calls["ingest"] == (paths, {"goal": 1})
        assert calls["fetch"] == (
            paths,
            {"VOO": (date(2026, 1, 1), None)},
            date(2026, 1, 1),
            date(2026, 4, 1),
            price_csv,
        )

    def test_print_summary_handles_empty_and_non_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        build_mod._print_summary([])
        assert capsys.readouterr().out == ""

        build_mod._print_summary([_alloc_row("2026-01-01", 10), _alloc_row("2026-01-02", 20)])

        out = capsys.readouterr().out
        assert "Earliest: 2026-01-01  $10" in out
        assert "Latest:   2026-01-02  $20" in out

    def test_build_source_config_injects_download_paths(self, paths: build_mod.BuildPaths) -> None:
        config = build_mod._build_source_config(paths, {"goal": 1})

        assert config["goal"] == 1
        assert config["fidelity_downloads"] == paths.downloads
        assert config["robinhood_downloads"] == paths.downloads
        assert config["empower_downloads"] == paths.downloads


class TestFullAndIncrementalBuild:
    def test_full_build_replaces_existing_rows_and_runs_precompute_validation(
        self,
        paths: build_mod.BuildPaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        init_db(paths.db_path)
        _seed_computed(paths.db_path, "2026-01-01", 999)
        calls: list[tuple[str, object]] = []
        _stub_full_build_inputs(monkeypatch, calls, allocation=[_alloc_row()], qianji_records=[{"id": "qj"}])
        monkeypatch.setattr(build_mod, "precompute_market", lambda db: calls.append(("market", db)))
        monkeypatch.setattr(build_mod, "precompute_holdings_detail", lambda db: calls.append(("holdings", db)))
        monkeypatch.setattr(build_mod, "_run_validation", lambda p: calls.append(("validation", p)))

        alloc = build_mod._full_build(
            paths,
            {"retirement_income_categories": ["401K"]},
            date(2026, 4, 1),
            date(2026, 4, 14),
        )

        assert alloc == [_alloc_row()]
        dates = db_rows(paths.db_path, "SELECT date, total FROM computed_daily ORDER BY date")
        tickers = db_rows(paths.db_path, "SELECT ticker FROM computed_daily_tickers")
        assert dates == [("2026-04-14", 100.0)]
        assert tickers == [("VOO",)]
        assert ("qianji", ([{"id": "qj"}], ["401K"])) in calls
        assert ("market", paths.db_path) in calls
        assert ("holdings", paths.db_path) in calls
        assert ("validation", paths) in calls

    def test_full_build_can_skip_market_and_validation(
        self,
        paths: build_mod.BuildPaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        init_db(paths.db_path)
        called: list[str] = []
        _stub_full_build_inputs(monkeypatch)
        monkeypatch.setattr(build_mod, "precompute_market", lambda db: called.append("market"))
        monkeypatch.setattr(build_mod, "precompute_holdings_detail", lambda db: called.append("holdings"))
        monkeypatch.setattr(build_mod, "_run_validation", lambda p: called.append("validation"))

        assert build_mod._full_build(
            paths,
            {},
            date(2026, 4, 1),
            date(2026, 4, 14),
            no_validate=True,
            skip_market_precompute=True,
        ) == []
        assert called == []

    def test_refresh_window_returns_empty_when_start_exceeds_end(
        self,
        paths: build_mod.BuildPaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        init_db(paths.db_path)
        _seed_computed(paths.db_path)
        monkeypatch.setattr(
            build_mod,
            "compute_daily_allocation",
            lambda *args: pytest.fail("compute should not run"),
        )

        result = build_mod._build_refresh_window(
            paths,
            {},
            date(2026, 4, 20),
            date(2026, 4, 14),
            no_validate=True,
        )

        assert result == []

    def test_refresh_window_can_skip_market_but_still_validate(
        self,
        paths: build_mod.BuildPaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        init_db(paths.db_path)
        _seed_computed(paths.db_path, "2026-04-13")
        calls: list[str] = []
        _stub_full_build_inputs(monkeypatch)
        monkeypatch.setattr(build_mod, "precompute_market", lambda db: calls.append("market"))
        monkeypatch.setattr(build_mod, "precompute_holdings_detail", lambda db: calls.append("holdings"))
        monkeypatch.setattr(build_mod, "_run_validation", lambda p: calls.append("validation"))

        assert build_mod._build_refresh_window(
            paths,
            {},
            date(2026, 1, 1),
            date(2026, 4, 14),
            skip_market_precompute=True,
        ) == []
        assert calls == ["validation"]

    def test_build_timemachine_db_threads_args_through_pipeline(
        self,
        paths: build_mod.BuildPaths,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[tuple[str, object]] = []
        args = argparse.Namespace(
            as_of=date(2026, 4, 14),
            prices_from_csv=paths.data_dir / "prices.csv",
            no_validate=True,
        )
        monkeypatch.setattr(build_mod, "_resolve_paths", lambda a: paths)
        monkeypatch.setattr(build_mod, "_load_config", lambda p: {"goal": 1})
        monkeypatch.setattr(
            build_mod,
            "_ingest_and_fetch",
            lambda p, cfg, end, *, prices_from_csv: calls.append(
                ("ingest", (p, cfg, end, prices_from_csv))
            ),
        )
        monkeypatch.setattr(build_mod, "_derive_start_date", lambda p, fallback: date(2026, 1, 1))
        monkeypatch.setattr(
            build_mod,
            "_build_refresh_window",
            lambda p, cfg, start, end, *, no_validate, skip_market_precompute: calls.append(
                ("refresh", (p, cfg, start, end, no_validate, skip_market_precompute))
            ),
        )

        assert build_mod.build_timemachine_db(args) == 0
        assert calls == [
            ("ingest", (paths, {"goal": 1}, date(2026, 4, 14), args.prices_from_csv)),
            ("refresh", (paths, {"goal": 1}, date(2026, 1, 1), date(2026, 4, 14), True, True)),
        ]
