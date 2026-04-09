"""Tests for precompute_market — market index + macro indicator precomputation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from generate_asset_snapshot.db import get_connection, ingest_prices, init_db
from generate_asset_snapshot.precompute import precompute_market

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
    """Verify that precompute_market writes correct rows into computed_market."""

    def test_writes_rows_for_each_index(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        rows = conn.execute(
            "SELECT ticker FROM computed_market WHERE ticker NOT LIKE '@_@_%' ESCAPE '@'"
        ).fetchall()
        conn.close()
        tickers = {r[0] for r in rows}
        assert tickers == {"^GSPC", "^NDX", "VXUS", "000300.SS"}

    def test_writes_usd_cny_row(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        row = conn.execute("SELECT current FROM computed_market WHERE ticker = '__usdCny'").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == pytest.approx(7.26, abs=0.01)

    def test_index_name_populated(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        row = conn.execute("SELECT name FROM computed_market WHERE ticker = '^GSPC'").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "S&P 500"

    def test_current_price_is_last_close(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        row = conn.execute("SELECT current FROM computed_market WHERE ticker = '^GSPC'").fetchone()
        conn.close()
        assert row is not None
        # base=5000, 300 days, last = 5000 + 299*0.5 = 5149.5
        assert row[0] == pytest.approx(5149.5, abs=0.01)


class TestSparkline:
    """Verify sparkline JSON structure."""

    def test_sparkline_is_valid_json_array(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        row = conn.execute("SELECT sparkline FROM computed_market WHERE ticker = '^GSPC'").fetchone()
        conn.close()
        assert row is not None
        parsed = json.loads(row[0])
        assert isinstance(parsed, list)
        assert len(parsed) > 0
        assert all(isinstance(v, (int, float)) for v in parsed)

    def test_sparkline_max_252_points(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        row = conn.execute("SELECT sparkline FROM computed_market WHERE ticker = '^GSPC'").fetchone()
        conn.close()
        parsed = json.loads(row[0])
        assert len(parsed) <= 252


class TestReturnsComputation:
    """Verify month return and YTD return calculations."""

    def test_month_return_computed(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        row = conn.execute("SELECT month_return FROM computed_market WHERE ticker = '^GSPC'").fetchone()
        conn.close()
        assert row is not None
        # With a steady uptrend, month return should be positive
        assert row[0] > 0

    def test_ytd_return_computed(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        row = conn.execute("SELECT ytd_return FROM computed_market WHERE ticker = '^GSPC'").fetchone()
        conn.close()
        assert row is not None
        # With a steady uptrend from start of year, YTD return should be positive
        assert row[0] > 0

    def test_52w_high_low(self, db_path: Path) -> None:
        precompute_market(db_path)
        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT high_52w, low_52w, current FROM computed_market WHERE ticker = '^GSPC'"
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

        row = conn.execute("SELECT month_return FROM computed_market WHERE ticker = '^GSPC'").fetchone()
        conn.close()
        assert row[0] == pytest.approx(expected_month, abs=0.01)


class TestClearAndRewrite:
    """Verify idempotent clear-and-rewrite behavior."""

    def test_rerun_does_not_duplicate(self, db_path: Path) -> None:
        precompute_market(db_path)
        precompute_market(db_path)
        conn = get_connection(db_path)
        count = conn.execute("SELECT COUNT(*) FROM computed_market").fetchone()[0]
        conn.close()
        # 4 indices + 1 __usdCny = 5
        assert count == 5


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
        idx_rows = conn.execute("SELECT ticker FROM computed_market WHERE ticker NOT LIKE '__%'").fetchall()
        conn.close()
        # ^GSPC has only 1 row, should be skipped; no other indices seeded
        assert len(idx_rows) == 0
