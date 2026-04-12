"""Extended tests for precompute: precompute_market and precompute_holdings_detail."""
from __future__ import annotations

from pathlib import Path

from generate_asset_snapshot.db import get_connection, init_db
from generate_asset_snapshot.precompute import (
    _compute_index_row,
    _precompute_cny,
    _precompute_fred,
    _precompute_indices,
    precompute_holdings_detail,
    precompute_market,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


def _seed_prices(db_path: Path, ticker: str, prices: list[tuple[str, float]]) -> None:
    conn = get_connection(db_path)
    for dt, close in prices:
        conn.execute(
            "INSERT OR REPLACE INTO daily_close (symbol, date, close) VALUES (?, ?, ?)",
            (ticker, dt, close),
        )
    conn.commit()
    conn.close()


def _seed_tickers(db_path: Path, tickers: list[tuple[str, str, float]]) -> None:
    """Insert (date, ticker, value) rows into computed_daily_tickers."""
    conn = get_connection(db_path)
    for dt, ticker, value in tickers:
        conn.execute(
            "INSERT INTO computed_daily_tickers (date, ticker, value, category) VALUES (?, ?, ?, 'US Equity')",
            (dt, ticker, value),
        )
    conn.commit()
    conn.close()


# ── _compute_index_row ──────────────────────────────────────────────────────


class TestComputeIndexRow:
    def test_returns_none_for_insufficient_data(self) -> None:
        rows = [("2025-01-02", 5000.0)]
        assert _compute_index_row("^GSPC", "S&P 500", rows) is None

    def test_returns_none_for_missing_ticker(self) -> None:
        assert _compute_index_row("MISSING", "Missing Index", []) is None

    def test_computes_returns_for_valid_data(self) -> None:
        # 30 trading days of steadily rising prices
        prices = [(f"2025-01-{d:02d}", 5000.0 + d * 10) for d in range(2, 32)]
        row = _compute_index_row("^GSPC", "S&P 500", prices)
        assert row is not None
        assert row.ticker == "^GSPC"
        assert row.name == "S&P 500"
        assert row.current == prices[-1][1]
        assert row.high_52w >= row.low_52w
        assert isinstance(row.month_return, float)
        assert isinstance(row.ytd_return, float)


# ── precompute_market ──────────────────────────────────────────────────────


class TestPrecomputeMarket:
    def test_stores_index_data(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        # Enough data for ^GSPC
        prices = [(f"2025-01-{d:02d}", 5000.0 + d) for d in range(2, 28)]
        _seed_prices(db_path, "^GSPC", prices)
        _seed_prices(db_path, "CNY=X", [("2025-01-27", 7.25)])

        precompute_market(db_path)

        conn = get_connection(db_path)
        rows = conn.execute("SELECT ticker, name FROM computed_market_indices").fetchall()
        conn.close()
        assert len(rows) >= 1
        assert rows[0][0] == "^GSPC"

    def test_stores_cny_rate(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        _seed_prices(db_path, "CNY=X", [("2025-01-10", 7.30)])

        precompute_market(db_path)

        conn = get_connection(db_path)
        row = conn.execute("SELECT value FROM computed_market_indicators WHERE key = 'usdCny'").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 7.30

    def test_handles_empty_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        precompute_market(db_path)  # should not raise

        conn = get_connection(db_path)
        rows = conn.execute("SELECT * FROM computed_market_indices").fetchall()
        conn.close()
        assert rows == []

    def test_clears_previous_data(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)

        # First run with data
        _seed_prices(db_path, "CNY=X", [("2025-01-10", 7.30)])
        precompute_market(db_path)

        # Second run without data -- should clear
        conn = get_connection(db_path)
        conn.execute("DELETE FROM daily_close")
        conn.commit()
        conn.close()
        precompute_market(db_path)

        conn = get_connection(db_path)
        rows = conn.execute("SELECT * FROM computed_market_indicators").fetchall()
        conn.close()
        assert rows == []


# ── _precompute_indices ─────────────────────────────────────────────────────


class TestPrecomputeIndices:
    def test_inserts_rows_for_seeded_indices(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        prices = [(f"2025-01-{d:02d}", 5000.0 + d) for d in range(2, 28)]
        _seed_prices(db_path, "^GSPC", prices)

        conn = get_connection(db_path)
        try:
            _precompute_indices(conn)
            conn.commit()
            rows = conn.execute("SELECT ticker FROM computed_market_indices").fetchall()
        finally:
            conn.close()
        assert ("^GSPC",) in rows

    def test_noop_for_unseeded_indices(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
        try:
            _precompute_indices(conn)
            conn.commit()
            rows = conn.execute("SELECT * FROM computed_market_indices").fetchall()
        finally:
            conn.close()
        assert rows == []


# ── _precompute_cny ─────────────────────────────────────────────────────────


class TestPrecomputeCny:
    def test_inserts_latest_rate(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        _seed_prices(db_path, "CNY=X", [("2025-01-01", 7.00), ("2025-01-10", 7.30)])

        conn = get_connection(db_path)
        try:
            _precompute_cny(conn)
            conn.commit()
            row = conn.execute(
                "SELECT value FROM computed_market_indicators WHERE key = 'usdCny'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == 7.30  # Latest date wins

    def test_noop_without_cny_data(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
        try:
            _precompute_cny(conn)  # should not raise
            conn.commit()
            rows = conn.execute("SELECT * FROM computed_market_indicators").fetchall()
        finally:
            conn.close()
        assert rows == []


# ── _precompute_fred ────────────────────────────────────────────────────────


class TestPrecomputeFred:
    def test_noop_without_api_key(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
        try:
            _precompute_fred(conn)  # should not raise
            conn.commit()
            rows = conn.execute("SELECT * FROM computed_market_indicators").fetchall()
        finally:
            conn.close()
        assert rows == []

    def test_inserts_snapshot_and_series_when_api_returns_data(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Snapshot keys are remapped per _FRED_SNAPSHOT_KEYS; series rows land in econ_series."""
        monkeypatch.setenv("FRED_API_KEY", "fake-key")
        fake_fred = {
            "snapshot": {
                "fedFundsRate": 5.25,
                "treasury10y": 4.50,
                "cpiYoy": 3.20,
                "unemployment": 3.70,
                "vix": 14.50,
            },
            "series": {
                "fedRate": [
                    {"date": "2025-01-01", "value": 5.25},
                    {"date": "2025-02-01", "value": 5.00},
                ],
                "cpi": [
                    {"date": "2025-01-01", "value": 3.20},
                ],
            },
        }
        monkeypatch.setattr(
            "generate_asset_snapshot.market.fred.fetch_fred_data",
            lambda _key: fake_fred,
        )

        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
        try:
            _precompute_fred(conn)
            conn.commit()

            indicators = dict(
                conn.execute("SELECT key, value FROM computed_market_indicators")
            )
            series_rows = conn.execute(
                "SELECT key, date, value FROM econ_series ORDER BY key, date"
            ).fetchall()
        finally:
            conn.close()

        # Snapshot keys get remapped per _FRED_SNAPSHOT_KEYS
        assert indicators == {
            "fedRate": 5.25,
            "treasury10y": 4.50,
            "cpi": 3.20,
            "unemployment": 3.70,
            "vix": 14.50,
        }
        # Series rows persist 1:1 (no remapping)
        assert series_rows == [
            ("cpi", "2025-01-01", 3.20),
            ("fedRate", "2025-01-01", 5.25),
            ("fedRate", "2025-02-01", 5.00),
        ]

    def test_none_from_api_is_noop(self, tmp_path: Path, monkeypatch) -> None:
        """fetch_fred_data returning None (total failure) should not insert or raise."""
        monkeypatch.setenv("FRED_API_KEY", "fake-key")
        monkeypatch.setattr(
            "generate_asset_snapshot.market.fred.fetch_fred_data",
            lambda _key: None,
        )
        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = get_connection(db_path)
        try:
            _precompute_fred(conn)
            conn.commit()
            rows = conn.execute("SELECT * FROM computed_market_indicators").fetchall()
        finally:
            conn.close()
        assert rows == []


# ── precompute_holdings_detail ─────────────────────────────────────────────


class TestPrecomputeHoldingsDetail:
    def test_computes_month_return(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        # ticker with 30 days of prices, ending at 110 (started at 100)
        prices = [(f"2025-01-{d:02d}", 100.0 + d * (10 / 30)) for d in range(1, 31)]
        _seed_prices(db_path, "VTI", prices)
        _seed_tickers(db_path, [("2025-01-30", "VTI", 55000.0)])

        precompute_holdings_detail(db_path)

        conn = get_connection(db_path)
        row = conn.execute("SELECT ticker, month_return FROM computed_holdings_detail").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "VTI"
        assert isinstance(row[1], float)

    def test_handles_empty_tickers(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        precompute_holdings_detail(db_path)  # should not raise

    def test_skips_non_ticker_symbols(self, tmp_path: Path) -> None:
        """Symbols with spaces or >5 chars should be excluded."""
        db_path = tmp_path / "test.db"
        init_db(db_path)
        _seed_tickers(db_path, [
            ("2025-01-30", "401k sp500", 50000.0),  # space -> skip
            ("2025-01-30", "LONGNAME", 50000.0),     # >5 chars -> skip
        ])
        precompute_holdings_detail(db_path)

        conn = get_connection(db_path)
        rows = conn.execute("SELECT * FROM computed_holdings_detail").fetchall()
        conn.close()
        assert rows == []

    def test_skips_tickers_without_enough_prices(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        _seed_tickers(db_path, [("2025-01-30", "VTI", 55000.0)])
        _seed_prices(db_path, "VTI", [("2025-01-30", 100.0)])  # only 1 price

        precompute_holdings_detail(db_path)

        conn = get_connection(db_path)
        rows = conn.execute("SELECT * FROM computed_holdings_detail").fetchall()
        conn.close()
        assert rows == []

    def test_computes_52w_high_low(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        prices = [(f"2025-01-{d:02d}", 100.0 + d) for d in range(1, 28)]
        _seed_prices(db_path, "VTI", prices)
        _seed_tickers(db_path, [("2025-01-27", "VTI", 55000.0)])

        precompute_holdings_detail(db_path)

        conn = get_connection(db_path)
        row = conn.execute("SELECT high_52w, low_52w, vs_high FROM computed_holdings_detail").fetchone()
        conn.close()
        assert row is not None
        high, low, vs_high = row
        assert high >= low
        assert vs_high <= 0  # current <= high → vs_high <= 0
