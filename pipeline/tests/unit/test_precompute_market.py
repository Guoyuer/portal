"""Tests for precompute_market and precompute_holdings_detail."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from etl.db import get_connection, init_db
from etl.precompute import precompute_holdings_detail, precompute_market
from tests.fixtures import ingest_prices


@pytest.fixture(autouse=True)
def _no_dxy_network():
    """Stub fetch_dxy_monthly so precompute_market doesn't hit Yahoo."""
    with patch("etl.market.yahoo.fetch_dxy_monthly", return_value=[]):
        yield

# ── Helpers ─────────────────────────────────────────────────────────────────


def _seed_index_prices(db_path: Path, ticker: str, base: float, n_days: int = 300) -> list[float]:
    """Insert n_days of synthetic daily_close rows for a ticker.

    Returns the list of close prices generated (ascending dates from 2025-01-02).
    """
    prices: dict[str, float] = {}
    closes: list[float] = []
    for i in range(n_days):
        # Generate a date string YYYY-MM-DD starting 2025-01-02
        day_offset = i
        d = f"2025-{(day_offset // 28) + 1:02d}-{(day_offset % 28) + 2:02d}"
        # Simple price: base + i to create an uptrend
        close = round(base + i * 0.5, 2)
        prices[d] = close
        closes.append(close)
    ingest_prices(db_path, {ticker: prices})
    return closes


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Create a fresh timemachine DB seeded with index + CNY prices."""
    p = tmp_path / "test.db"
    init_db(p)

    # Seed the four index tickers
    _seed_index_prices(p, "^GSPC", 5000.0, 300)
    _seed_index_prices(p, "^NDX", 18000.0, 300)
    _seed_index_prices(p, "VXUS", 55.0, 300)
    _seed_index_prices(p, "000300.SS", 3500.0, 300)

    # Seed CNY=X rate
    ingest_prices(p, {"CNY=X": {"2025-10-01": 7.25, "2025-10-02": 7.26}})

    return p


# ── Tests ───────────────────────────────────────────────────────────────────


class TestPrecomputeMarketRows:
    """Verify that precompute_market writes correct rows into computed_market_indices."""

    def test_writes_rows_for_each_index(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        rows = conn.execute("SELECT ticker FROM computed_market_indices").fetchall()
        conn.close()
        tickers = {r[0] for r in rows}
        assert tickers == {"^GSPC", "^NDX", "VXUS", "000300.SS"}

    def test_writes_usd_cny_indicator(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT value FROM econ_series WHERE key='usdCny' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == pytest.approx(7.26, abs=0.01)

    def test_index_name_populated(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        row = conn.execute("SELECT name FROM computed_market_indices WHERE ticker = '^GSPC'").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "S&P 500"

    def test_current_price_is_last_close(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        row = conn.execute("SELECT current FROM computed_market_indices WHERE ticker = '^GSPC'").fetchone()
        conn.close()
        assert row is not None
        # base=5000, 300 days, last = 5000 + 299*0.5 = 5149.5
        assert row[0] == pytest.approx(5149.5, abs=0.01)


class TestSparkline:
    """Verify sparkline JSON structure."""

    def test_sparkline_is_valid_json_array(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        row = conn.execute("SELECT sparkline FROM computed_market_indices WHERE ticker = '^GSPC'").fetchone()
        conn.close()
        assert row is not None
        parsed = json.loads(row[0])
        assert isinstance(parsed, list)
        assert len(parsed) > 0
        assert all(isinstance(v, (int, float)) for v in parsed)

    def test_sparkline_max_252_points(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        row = conn.execute("SELECT sparkline FROM computed_market_indices WHERE ticker = '^GSPC'").fetchone()
        conn.close()
        parsed = json.loads(row[0])
        assert len(parsed) <= 252


class TestReturnsComputation:
    """Verify month return and YTD return calculations."""

    def test_month_return_computed(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        row = conn.execute("SELECT month_return FROM computed_market_indices WHERE ticker = '^GSPC'").fetchone()
        conn.close()
        assert row is not None
        # With a steady uptrend, month return should be positive
        assert row[0] > 0

    def test_ytd_return_computed(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        row = conn.execute("SELECT ytd_return FROM computed_market_indices WHERE ticker = '^GSPC'").fetchone()
        conn.close()
        assert row is not None
        # With a steady uptrend from start of year, YTD return should be positive
        assert row[0] > 0

    def test_52w_high_low(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT high_52w, low_52w, current FROM computed_market_indices WHERE ticker = '^GSPC'"
        ).fetchone()
        conn.close()
        assert row is not None
        high_52w, low_52w, current = row
        # High should be >= current, low should be <= current
        assert high_52w >= current
        assert low_52w <= current
        assert high_52w > low_52w

    def test_month_return_correct_value(self, db_path: Path) -> None:
        """Verify exact month return against manual calculation."""
        precompute_market(db_path)
        conn = get_connection(db_path)

        # Get the raw closes for ^GSPC to compute expected value
        closes_rows = conn.execute(
            "SELECT close FROM daily_close WHERE symbol = '^GSPC' ORDER BY date"
        ).fetchall()
        closes = [r[0] for r in closes_rows]
        current = closes[-1]
        month_idx = max(0, len(closes) - 23)
        expected_month = round((current / closes[month_idx] - 1) * 100, 2)

        row = conn.execute("SELECT month_return FROM computed_market_indices WHERE ticker = '^GSPC'").fetchone()
        conn.close()
        assert row[0] == pytest.approx(expected_month, abs=0.01)


class TestClearAndRewrite:
    """Verify idempotent clear-and-rewrite behavior."""

    def test_rerun_does_not_duplicate(self, db_path: Path) -> None:
        precompute_market(db_path)
        precompute_market(db_path)
        conn = get_connection(db_path)
        idx_count = conn.execute("SELECT COUNT(*) FROM computed_market_indices").fetchone()[0]
        cny_count = conn.execute("SELECT COUNT(*) FROM econ_series WHERE key='usdCny'").fetchone()[0]
        conn.close()
        assert idx_count == 4  # 4 indices
        # Fixture seeds 2 CNY rows both in 2025-10 → 1 monthly bucket
        assert cny_count == 1


class TestSkipTickerWithTooFewRows:
    """Verify that tickers with < 2 rows are skipped gracefully."""

    def test_skip_ticker_with_one_row(self, tmp_path: Path) -> None:
        db_path = tmp_path / "sparse.db"
        init_db(db_path)
        # Only one price row for ^GSPC
        ingest_prices(db_path, {"^GSPC": {"2025-06-01": 5000.0}})
        ingest_prices(db_path, {"CNY=X": {"2025-06-01": 7.25}})
        precompute_market(db_path)
        conn = get_connection(db_path)
        idx_rows = conn.execute("SELECT ticker FROM computed_market_indices").fetchall()
        conn.close()
        # ^GSPC has only 1 row, should be skipped; no other indices seeded
        assert len(idx_rows) == 0


# ── Holdings detail precomputation tests ───────────────────────────────────


def _seed_holdings_db(db_path: Path) -> None:
    """Populate computed_daily_tickers and daily_close for holdings detail tests.

    Creates two real tickers (VOO, QQQM) and one fake ("401k sp500").
    Prices: 300 rows of steady uptrend so month_return and 52w calcs work.
    """
    conn = get_connection(db_path)
    latest = "2025-11-01"

    # Insert ticker values on the latest date
    conn.executemany(
        "INSERT INTO computed_daily_tickers (date, ticker, value, category, subtype) VALUES (?, ?, ?, ?, ?)",
        [
            (latest, "VOO", 50000.0, "US Equity", "etf"),
            (latest, "QQQM", 30000.0, "US Equity", "etf"),
            (latest, "401k sp500", 20000.0, "US Equity", "401k"),   # fake — has space, >5 chars
            (latest, "CNY Cash", 5000.0, "Non-US Equity", ""),    # fake — has space
        ],
    )
    conn.commit()
    conn.close()

    # Seed daily_close for VOO and QQQM (300 days uptrend)
    _seed_index_prices(db_path, "VOO", 400.0, 300)
    _seed_index_prices(db_path, "QQQM", 170.0, 300)


@pytest.fixture()
def holdings_db(tmp_path: Path) -> Path:
    """DB with computed_daily_tickers + daily_close for holdings detail."""
    p = tmp_path / "holdings.db"
    init_db(p)
    _seed_holdings_db(p)
    return p


class TestPrecomputeHoldingsDetailRows:
    """Verify that precompute_holdings_detail writes correct rows."""

    def test_writes_rows_for_real_tickers_only(self, holdings_db: Path) -> None:
        precompute_holdings_detail(holdings_db)
        conn = get_connection(holdings_db)
        rows = conn.execute("SELECT ticker FROM computed_holdings_detail ORDER BY ticker").fetchall()
        conn.close()
        tickers = {r[0] for r in rows}
        assert "VOO" in tickers
        assert "QQQM" in tickers
        assert "401k sp500" not in tickers
        assert "CNY Cash" not in tickers

    def test_end_value_matches_ticker_value(self, holdings_db: Path) -> None:
        precompute_holdings_detail(holdings_db)
        conn = get_connection(holdings_db)
        row = conn.execute("SELECT end_value FROM computed_holdings_detail WHERE ticker = 'VOO'").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == pytest.approx(50000.0, abs=0.01)

    def test_month_return_positive_uptrend(self, holdings_db: Path) -> None:
        precompute_holdings_detail(holdings_db)
        conn = get_connection(holdings_db)
        row = conn.execute("SELECT month_return FROM computed_holdings_detail WHERE ticker = 'VOO'").fetchone()
        conn.close()
        assert row is not None
        assert row[0] > 0  # steady uptrend -> positive month return

    def test_month_return_correct_value(self, holdings_db: Path) -> None:
        """Verify month return matches manual calculation from seeded prices."""
        precompute_holdings_detail(holdings_db)
        conn = get_connection(holdings_db)

        # Recompute expected: last 300 prices of VOO, base=400 step=0.5
        closes = conn.execute(
            "SELECT close FROM daily_close WHERE symbol = 'VOO' ORDER BY date"
        ).fetchall()
        prices = [r[0] for r in closes]
        current = prices[-1]
        month_idx = max(0, len(prices) - 23)
        expected = round((current / prices[month_idx] - 1) * 100, 2)

        row = conn.execute("SELECT month_return FROM computed_holdings_detail WHERE ticker = 'VOO'").fetchone()
        conn.close()
        assert row[0] == pytest.approx(expected, abs=0.01)

    def test_52w_high_low_and_vs_high(self, holdings_db: Path) -> None:
        precompute_holdings_detail(holdings_db)
        conn = get_connection(holdings_db)
        row = conn.execute(
            "SELECT high_52w, low_52w, vs_high FROM computed_holdings_detail WHERE ticker = 'VOO'"
        ).fetchone()
        conn.close()
        assert row is not None
        high_52w, low_52w, vs_high = row
        assert high_52w >= low_52w
        assert vs_high <= 0  # current <= high, so vs_high <= 0

    def test_start_value_derived_from_month_return(self, holdings_db: Path) -> None:
        precompute_holdings_detail(holdings_db)
        conn = get_connection(holdings_db)
        row = conn.execute(
            "SELECT month_return, start_value, end_value FROM computed_holdings_detail WHERE ticker = 'VOO'"
        ).fetchone()
        conn.close()
        month_ret, start_val, end_val = row
        # start_value = end_value / (1 + month_ret / 100)
        expected_start = round(end_val / (1 + month_ret / 100), 2)
        assert start_val == pytest.approx(expected_start, abs=0.01)


class TestHoldingsDetailIdempotent:
    """Verify clear-and-rewrite: no duplicates on re-run."""

    def test_rerun_does_not_duplicate(self, holdings_db: Path) -> None:
        precompute_holdings_detail(holdings_db)
        precompute_holdings_detail(holdings_db)
        conn = get_connection(holdings_db)
        count = conn.execute("SELECT COUNT(*) FROM computed_holdings_detail").fetchone()[0]
        conn.close()
        # Only VOO + QQQM = 2
        assert count == 2


class TestHoldingsDetailEmptyDB:
    """Verify graceful handling when no data exists."""

    def test_no_crash_on_empty_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "empty.db"
        init_db(db_path)
        precompute_holdings_detail(db_path)
        conn = get_connection(db_path)
        count = conn.execute("SELECT COUNT(*) FROM computed_holdings_detail").fetchone()[0]
        conn.close()
        assert count == 0
