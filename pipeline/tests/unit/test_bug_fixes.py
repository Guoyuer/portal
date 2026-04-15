"""Regression tests for ingestion-pipeline invariants that previously broke.

Each class pins one invariant (replay ordering, holding-period start, missing-
price warning, unmapped-account warning, T-Bill CUSIP handling). Originally
authored against archived bug reports; the names now describe behavior.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# Stub yfinance so prices.py imports without error
if "yfinance" not in sys.modules:
    _yf = MagicMock(spec=ModuleType, __name__="yfinance", __path__=[])
    _yf.download = MagicMock(return_value=MagicMock(empty=True))
    sys.modules["yfinance"] = _yf

from etl.allocation import compute_daily_allocation  # noqa: E402
from etl.db import init_db  # noqa: E402
from etl.prices import symbol_holding_periods_from_db  # noqa: E402
from etl.timemachine import replay_from_db  # noqa: E402

# ── Helpers ────────────────────────────────────────────────────────────────


def _insert_txn(
    conn: sqlite3.Connection,
    run_date: str,
    acct_num: str,
    action: str,
    symbol: str,
    qty: float,
    amount: float,
    *,
    lot_type: str = "",
    account: str = "Taxable",
) -> None:
    """Insert a single fidelity_transactions row."""
    conn.execute(
        "INSERT INTO fidelity_transactions"
        " (run_date, account, account_number, action, action_type, symbol,"
        "  lot_type, quantity, price, amount, settlement_date)"
        " VALUES (?, ?, ?, ?, '', ?, ?, ?, 0, ?, '')",
        (run_date, account, acct_num, action, symbol, lot_type, qty, amount),
    )


def _init_qianji(db_path: Path, assets: list[tuple[str, float, str]]) -> None:
    """Create a minimal Qianji DB with given assets."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS user_asset"
        " (name TEXT PRIMARY KEY, money REAL, currency TEXT, status INTEGER DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS user_bill"
        " (time INTEGER, type INTEGER, money REAL,"
        "  fromact TEXT DEFAULT '', targetact TEXT DEFAULT '',"
        "  extra TEXT DEFAULT '', status INTEGER DEFAULT 1)"
    )
    for name, money, currency in assets:
        conn.execute(
            "INSERT INTO user_asset (name, money, currency) VALUES (?, ?, ?)",
            (name, money, currency),
        )
    conn.commit()
    conn.close()


# ── BUG 1: Cost basis wrong due to ORDER BY id ───────────────────────────


class TestCostBasisOrderedByDate:
    """replay_from_db must produce correct cost basis regardless of insertion order.

    Root cause: ORDER BY id processes sells before buys when the buy was
    imported in a later CSV batch (higher id, earlier date).
    """

    def test_sell_after_buy_in_id_order_but_before_in_date_order(self, tmp_path: Path) -> None:
        """When a sell has a lower id than its corresponding buy (because the
        buy was imported later), cost basis must still be reduced by the sell.
        """
        db = tmp_path / "tm.db"
        init_db(db)
        conn = sqlite3.connect(str(db))

        # Insert in NON-chronological order (simulating overlapping CSV imports):
        # id=1: SELL 5 shares on 01/10 (from earlier CSV import, lower id)
        _insert_txn(conn, "2025-01-10", "Z123", "YOU SOLD STOCK", "AAPL", -5, 3000)
        # id=2: BUY 10 shares on 01/02 (from later CSV import, higher id)
        _insert_txn(conn, "2025-01-02", "Z123", "YOU BOUGHT STOCK", "AAPL", 10, -5000)

        conn.commit()
        conn.close()

        result = replay_from_db(db, date(2025, 1, 15))

        # Position: 10 - 5 = 5 shares (order doesn't matter for qty)
        assert result["positions"][("Z123", "AAPL")] == pytest.approx(5.0)

        # Cost basis: bought $5000 for 10 shares, sold 5/10 = 50%
        # Correct CB = $5000 * (1 - 0.5) = $2500
        assert result["cost_basis"][("Z123", "AAPL")] == pytest.approx(2500.0)

    def test_full_sell_zeroes_cost_basis_regardless_of_id_order(self, tmp_path: Path) -> None:
        """Selling all shares must zero out cost basis even when the sell
        has a lower id than the buy.
        """
        db = tmp_path / "tm.db"
        init_db(db)
        conn = sqlite3.connect(str(db))

        # Sell processed first by id, buy processed second
        _insert_txn(conn, "2025-01-10", "Z123", "YOU SOLD ALL", "VOO", -10, 6000)
        _insert_txn(conn, "2025-01-02", "Z123", "YOU BOUGHT X", "VOO", 10, -5000)

        conn.commit()
        conn.close()

        result = replay_from_db(db, date(2025, 1, 15))

        # Position: 0 shares
        assert ("Z123", "VOO") not in result["positions"]
        # Cost basis: should be 0 (fully sold)
        assert result["cost_basis"].get(("Z123", "VOO"), 0) == pytest.approx(0.0)

    def test_multiple_buys_then_sell_out_of_id_order(self, tmp_path: Path) -> None:
        """Multiple buys + a sell, all with scrambled ids."""
        db = tmp_path / "tm.db"
        init_db(db)
        conn = sqlite3.connect(str(db))

        # id=1: SELL on 03/01 (lowest id, middle date)
        _insert_txn(conn, "2025-03-01", "Z123", "YOU SOLD X", "TSLA", -5, 2500)
        # id=2: BUY on 02/01 (middle id, earliest date)
        _insert_txn(conn, "2025-02-01", "Z123", "YOU BOUGHT X", "TSLA", 10, -4000)
        # id=3: BUY on 04/01 (highest id, latest date)
        _insert_txn(conn, "2025-04-01", "Z123", "YOU BOUGHT X", "TSLA", 5, -2000)

        conn.commit()
        conn.close()

        result = replay_from_db(db, date(2025, 4, 15))

        # Position: 10 - 5 + 5 = 10
        assert result["positions"][("Z123", "TSLA")] == pytest.approx(10.0)

        # Chronological: buy 10 ($4000), sell 5 (50% of 10 → CB -= $2000 → $2000), buy 5 ($2000)
        # Final CB = $2000 + $2000 = $4000
        assert result["cost_basis"][("Z123", "TSLA")] == pytest.approx(4000.0)


# ── BUG 2: first_held date wrong due to ORDER BY id ──────────────────────


class TestHoldingPeriodIsEarliestDate:
    """symbol_holding_periods_from_db must return the chronologically earliest
    transaction date, not the lowest-id transaction date.
    """

    def test_first_held_is_earliest_date_not_lowest_id(self, tmp_path: Path) -> None:
        """When the earliest-by-date transaction has a higher id than a
        later-by-date transaction, first_held must still be the earliest date.
        """
        db = tmp_path / "tm.db"
        init_db(db)
        conn = sqlite3.connect(str(db))

        # id=1: reinvestment on 12/24 (low id, late date)
        _insert_txn(conn, "2025-12-24", "Z123", "REINVESTMENT", "SGOV", 0.5, -50)
        # id=2: buy on 11/04 (high id, early date)
        _insert_txn(conn, "2025-11-04", "Z123", "YOU BOUGHT X", "SGOV", 240, -24096)

        conn.commit()
        conn.close()

        periods = symbol_holding_periods_from_db(db)

        assert "SGOV" in periods
        first_held, _ = periods["SGOV"]
        assert first_held == date(2025, 11, 4)  # NOT 2025-12-24

    def test_multiple_symbols_all_correct(self, tmp_path: Path) -> None:
        """Multiple symbols with scrambled ids all get correct first_held."""
        db = tmp_path / "tm.db"
        init_db(db)
        conn = sqlite3.connect(str(db))

        # VTEB: id=1 is late, id=4 is early
        _insert_txn(conn, "2025-12-22", "Z123", "REINVESTMENT", "VTEB", 0.6, -30)
        # GLDM: id=2 is late, id=5 is early
        _insert_txn(conn, "2025-12-29", "Z123", "YOU BOUGHT X", "GLDM", 4, -340)
        # VOO: id=3 is correct (only one txn)
        _insert_txn(conn, "2024-06-17", "Z123", "YOU BOUGHT X", "VOO", 1, -500)
        # VTEB early
        _insert_txn(conn, "2025-12-04", "Z123", "YOU BOUGHT X", "VTEB", 219, -11000)
        # GLDM early
        _insert_txn(conn, "2025-10-29", "Z123", "YOU BOUGHT X", "GLDM", 12, -950)

        conn.commit()
        conn.close()

        periods = symbol_holding_periods_from_db(db)

        assert periods["VTEB"][0] == date(2025, 12, 4)   # NOT 12/22
        assert periods["GLDM"][0] == date(2025, 10, 29)  # NOT 12/29
        assert periods["VOO"][0] == date(2024, 6, 17)     # unchanged (single txn)


# ── BUG 3: Amex HYSA config ─────────────────────────────────────────────
# (Config-level fix; tested via allocation integration in Bug 5 tests)


# ── BUG 4: Positions without prices silently dropped ─────────────────────


class TestMissingPriceWarns:
    """When a Fidelity position has shares but no price data, the pipeline
    must log a warning instead of silently dropping the value.
    """

    def test_warns_on_position_without_price(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """A position with shares but no price data must produce a warning."""
        db = tmp_path / "tm.db"
        qj = tmp_path / "qj.db"
        init_db(db)
        _init_qianji(qj, [])

        conn = sqlite3.connect(str(db))
        # Buy SGOV — but don't add SGOV to daily_close prices
        _insert_txn(conn, "2025-01-02", "Z123", "YOU BOUGHT X", "SGOV", 240, -24096)
        # Add price for a DIFFERENT ticker so prices_df is non-empty
        conn.execute("INSERT INTO daily_close VALUES ('VTI', '2025-01-02', 250.0)")
        conn.execute("INSERT INTO daily_close VALUES ('CNY=X', '2025-01-02', 7.25)")
        conn.commit()
        conn.close()

        config = {
            "assets": {"SGOV": {"category": "Safe Net"}, "VTI": {"category": "US Equity"}},
            "qianji_accounts": {
                "fidelity_tracked": [],
                "ticker_map": {},
            },
        }

        # After the data-source abstraction refactor, the missing-price
        # warning is emitted by ``etl.sources.fidelity`` rather than
        # ``etl.allocation``. Watch the root logger so either location is
        # caught.
        with caplog.at_level(logging.WARNING):
            compute_daily_allocation(db, qj, config, date(2025, 1, 2), date(2025, 1, 2))

        # Must warn about SGOV having no price
        sgov_warnings = [r for r in caplog.records if "SGOV" in r.message]
        assert len(sgov_warnings) > 0, "Expected a warning about SGOV missing price"

    def test_position_with_price_no_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """A position WITH price data must not produce a missing-price warning."""
        db = tmp_path / "tm.db"
        qj = tmp_path / "qj.db"
        init_db(db)
        _init_qianji(qj, [])

        conn = sqlite3.connect(str(db))
        _insert_txn(conn, "2025-01-02", "Z123", "YOU BOUGHT X", "VTI", 10, -2500)
        conn.execute("INSERT INTO daily_close VALUES ('VTI', '2025-01-02', 250.0)")
        conn.execute("INSERT INTO daily_close VALUES ('CNY=X', '2025-01-02', 7.25)")
        conn.commit()
        conn.close()

        config = {
            "assets": {"VTI": {"category": "US Equity"}},
            "qianji_accounts": {"fidelity_tracked": [], "ticker_map": {}},
        }

        with caplog.at_level(logging.WARNING):
            compute_daily_allocation(db, qj, config, date(2025, 1, 2), date(2025, 1, 2))

        price_warnings = [r for r in caplog.records if "price" in r.message.lower()]
        assert len(price_warnings) == 0


# ── BUG 5: Unmapped Qianji accounts silently dropped ────────────────────


class TestUnmappedQianjiWarns:
    """When a USD Qianji account has a positive balance but is not in
    ticker_map, the pipeline must log a warning.
    """

    def test_warns_on_unmapped_usd_account(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """An unmapped USD account with meaningful balance must produce a warning."""
        db = tmp_path / "tm.db"
        qj = tmp_path / "qj.db"
        init_db(db)
        _init_qianji(qj, [("Amex HYSA", 20000.0, "USD")])

        conn = sqlite3.connect(str(db))
        # Need at least one Fidelity txn to establish date range
        _insert_txn(conn, "2025-01-02", "Z123", "YOU BOUGHT X", "VTI", 10, -2500)
        conn.execute("INSERT INTO daily_close VALUES ('VTI', '2025-01-02', 250.0)")
        conn.execute("INSERT INTO daily_close VALUES ('CNY=X', '2025-01-02', 7.25)")
        conn.commit()
        conn.close()

        config = {
            "assets": {"VTI": {"category": "US Equity"}},
            "qianji_accounts": {
                "fidelity_tracked": [],
                "ticker_map": {},  # NO mapping for "Amex HYSA"
            },
        }

        with caplog.at_level(logging.WARNING, logger="etl.allocation"):
            compute_daily_allocation(db, qj, config, date(2025, 1, 2), date(2025, 1, 2))

        hysa_warnings = [r for r in caplog.records if "Amex HYSA" in r.message]
        assert len(hysa_warnings) > 0, "Expected a warning about unmapped Amex HYSA"

    def test_mapped_account_no_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """A properly mapped Qianji account must not produce a warning."""
        db = tmp_path / "tm.db"
        qj = tmp_path / "qj.db"
        init_db(db)
        _init_qianji(qj, [("HYSA", 5000.0, "USD")])

        conn = sqlite3.connect(str(db))
        _insert_txn(conn, "2025-01-02", "Z123", "YOU BOUGHT X", "VTI", 10, -2500)
        conn.execute("INSERT INTO daily_close VALUES ('VTI', '2025-01-02', 250.0)")
        conn.execute("INSERT INTO daily_close VALUES ('CNY=X', '2025-01-02', 7.25)")
        conn.commit()
        conn.close()

        config = {
            "assets": {"VTI": {"category": "US Equity"}, "HYSA": {"category": "Safe Net"}},
            "qianji_accounts": {
                "fidelity_tracked": [],
                "ticker_map": {"HYSA": "HYSA"},  # mapped
            },
        }

        with caplog.at_level(logging.WARNING, logger="etl.allocation"):
            compute_daily_allocation(db, qj, config, date(2025, 1, 2), date(2025, 1, 2))

        unmapped_warnings = [r for r in caplog.records if "ticker_map" in r.message.lower() or "unmapped" in r.message.lower()]
        assert len(unmapped_warnings) == 0

    def test_cny_account_not_warned(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """CNY accounts without ticker_map entry go to CNY Assets — no warning."""
        db = tmp_path / "tm.db"
        qj = tmp_path / "qj.db"
        init_db(db)
        _init_qianji(qj, [("Alipay", 10000.0, "CNY")])

        conn = sqlite3.connect(str(db))
        _insert_txn(conn, "2025-01-02", "Z123", "YOU BOUGHT X", "VTI", 10, -2500)
        conn.execute("INSERT INTO daily_close VALUES ('VTI', '2025-01-02', 250.0)")
        conn.execute("INSERT INTO daily_close VALUES ('CNY=X', '2025-01-02', 7.25)")
        conn.commit()
        conn.close()

        config = {
            "assets": {"VTI": {"category": "US Equity"}, "CNY Assets": {"category": "Safe Net"}},
            "qianji_accounts": {
                "fidelity_tracked": [],
                "ticker_map": {},  # no mapping for Alipay — but it's CNY, so OK
            },
        }

        with caplog.at_level(logging.WARNING, logger="etl.allocation"):
            compute_daily_allocation(db, qj, config, date(2025, 1, 2), date(2025, 1, 2))

        # CNY accounts should NOT trigger unmapped warnings
        unmapped = [r for r in caplog.records if "Alipay" in r.message and "ticker_map" in r.message.lower()]
        assert len(unmapped) == 0


# ── BUG 6: T-Bills (CUSIPs) have no price → missing from net worth ──────


class TestTBillCusipsValuedAtFace:
    """T-Bill positions use CUSIPs (e.g. 912797FY8) which have no yfinance
    price data. They should be valued at face value ($1/unit) and categorized
    as Safe Net.
    """

    def test_tbill_cusip_valued_at_face_value(self, tmp_path: Path) -> None:
        """A T-Bill CUSIP position should appear in allocation at qty * $1."""
        db = tmp_path / "tm.db"
        qj = tmp_path / "qj.db"
        init_db(db)
        _init_qianji(qj, [])

        conn = sqlite3.connect(str(db))
        # Buy 3000 units of a T-Bill CUSIP
        _insert_txn(conn, "2025-01-02", "Z123", "YOU BOUGHT X", "912796CR8", 3000, -2930)
        conn.execute("INSERT INTO daily_close VALUES ('CNY=X', '2025-01-02', 7.25)")
        conn.commit()
        conn.close()

        config = {
            "assets": {"T-Bills": {"category": "Safe Net"}},
            "qianji_accounts": {"fidelity_tracked": [], "ticker_map": {}},
        }

        results = compute_daily_allocation(db, qj, config, date(2025, 1, 2), date(2025, 1, 2))

        day = results[0]
        tickers = {t["ticker"]: t for t in day["tickers"]}
        assert "T-Bills" in tickers
        assert tickers["T-Bills"]["value"] == pytest.approx(3000.0)
        assert tickers["T-Bills"]["category"] == "Safe Net"

    def test_brokered_cd_cusip_valued_at_face_value(self, tmp_path: Path) -> None:
        """A brokered CD CUSIP (non-912) should also be valued at face."""
        db = tmp_path / "tm.db"
        qj = tmp_path / "qj.db"
        init_db(db)
        _init_qianji(qj, [])

        conn = sqlite3.connect(str(db))
        # JPMorgan CD CUSIP — NOT starting with 912
        _insert_txn(conn, "2025-01-02", "Z123", "YOU BOUGHT X", "46656MQ38", 4000, -4000)
        conn.execute("INSERT INTO daily_close VALUES ('CNY=X', '2025-01-02', 7.25)")
        conn.commit()
        conn.close()

        config = {
            "assets": {"T-Bills": {"category": "Safe Net"}},
            "qianji_accounts": {"fidelity_tracked": [], "ticker_map": {}},
        }

        results = compute_daily_allocation(db, qj, config, date(2025, 1, 2), date(2025, 1, 2))

        tickers = {t["ticker"]: t for t in results[0]["tickers"]}
        assert "T-Bills" in tickers
        assert tickers["T-Bills"]["value"] == pytest.approx(4000.0)

    def test_cusip_no_missing_price_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """CUSIP positions should not trigger missing-price warnings."""
        db = tmp_path / "tm.db"
        qj = tmp_path / "qj.db"
        init_db(db)
        _init_qianji(qj, [])

        conn = sqlite3.connect(str(db))
        _insert_txn(conn, "2025-01-02", "Z123", "YOU BOUGHT X", "912797FY8", 1000, -984)
        _insert_txn(conn, "2025-01-02", "Z123", "YOU BOUGHT X", "06428FG68", 4000, -4000)
        conn.execute("INSERT INTO daily_close VALUES ('CNY=X', '2025-01-02', 7.25)")
        conn.commit()
        conn.close()

        config = {
            "assets": {"T-Bills": {"category": "Safe Net"}},
            "qianji_accounts": {"fidelity_tracked": [], "ticker_map": {}},
        }

        with caplog.at_level(logging.WARNING, logger="etl.allocation"):
            compute_daily_allocation(db, qj, config, date(2025, 1, 2), date(2025, 1, 2))

        cusip_warnings = [r for r in caplog.records if "912797" in r.message or "06428" in r.message]
        assert len(cusip_warnings) == 0

    def test_multiple_cusips_aggregated(self, tmp_path: Path) -> None:
        """T-Bills + brokered CDs should all aggregate into one T-Bills ticker."""
        db = tmp_path / "tm.db"
        qj = tmp_path / "qj.db"
        init_db(db)
        _init_qianji(qj, [])

        conn = sqlite3.connect(str(db))
        _insert_txn(conn, "2025-01-02", "Z123", "YOU BOUGHT X", "912796CR8", 3000, -2930)
        _insert_txn(conn, "2025-01-02", "Z123", "YOU BOUGHT X", "06428FG68", 4000, -4000)
        conn.execute("INSERT INTO daily_close VALUES ('CNY=X', '2025-01-02', 7.25)")
        conn.commit()
        conn.close()

        config = {
            "assets": {"T-Bills": {"category": "Safe Net"}},
            "qianji_accounts": {"fidelity_tracked": [], "ticker_map": {}},
        }

        results = compute_daily_allocation(db, qj, config, date(2025, 1, 2), date(2025, 1, 2))

        tickers = {t["ticker"]: t for t in results[0]["tickers"]}
        assert tickers["T-Bills"]["value"] == pytest.approx(7000.0)
        assert results[0]["safe_net"] == pytest.approx(7000.0)
