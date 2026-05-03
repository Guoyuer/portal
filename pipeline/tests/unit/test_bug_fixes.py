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

from dataclasses import replace  # noqa: E402

from etl.allocation import compute_daily_allocation  # noqa: E402
from etl.db import init_db  # noqa: E402
from etl.prices import symbol_holding_periods_from_db  # noqa: E402
from etl.replay import replay_transactions  # noqa: E402
from etl.sources.fidelity import FIDELITY_REPLAY, classify_fidelity_action  # noqa: E402
from tests.fixtures import connected_db, insert_fidelity_txn  # noqa: E402

# Strip cash tracking — these tests only assert position quantities + cost
# basis, not cash ledgers.
_FIDELITY_POSITIONS_ONLY = replace(
    FIDELITY_REPLAY, track_cash=False, lot_type_col=None, mm_drip_tickers=frozenset(),
)


def _replay_fidelity(db: Path, as_of: date) -> dict[tuple[str, str], tuple[float, float]]:
    """Return ``{(account, symbol): (qty, cost_basis)}`` for the shared replay primitive."""
    result = replay_transactions(db, _FIDELITY_POSITIONS_ONLY, as_of)
    return {key: (st.quantity, st.cost_basis_usd) for key, st in result.positions.items()}


# ── Helpers ────────────────────────────────────────────────────────────────


def _insert_txn(
    conn: sqlite3.Connection,
    run_date: str,
    acct_num: str,
    action: str,
    symbol: str,
    qty: float,
    amount: float,
) -> None:
    insert_fidelity_txn(
        conn,
        run_date=run_date,
        account_number=acct_num,
        action=action,
        action_kind=classify_fidelity_action(action).value,
        symbol=symbol,
        quantity=qty,
        amount=amount,
    )


# Each tuple: (run_date, account, action, symbol, quantity, amount).
Txn = tuple[str, str, str, str, float, float]


def _seed_fidelity_db(tmp_path: Path, txns: list[Txn]) -> Path:
    """Init a timemachine.db and insert the given fidelity transactions."""
    db = tmp_path / "tm.db"
    init_db(db)
    with connected_db(db) as conn:
        for run_date, acct, action, sym, qty, amt in txns:
            _insert_txn(conn, run_date, acct, action, sym, qty, amt)
    return db


def _init_qianji(db_path: Path, assets: list[tuple[str, float, str]]) -> None:
    """Create a minimal Qianji DB with given assets."""
    with sqlite3.connect(str(db_path)) as conn:
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


def _allocation_setup(
    tmp_path: Path,
    *,
    txns: list[Txn],
    extra_prices: list[tuple[str, str, float]] = (),
    qianji_assets: list[tuple[str, float, str]] = (),
) -> tuple[Path, Path]:
    """Common scaffold: timemachine DB + Qianji DB + ``CNY=X`` rate.

    ``extra_prices`` rows are inserted into ``daily_close``; the standard
    ``CNY=X`` rate is always added so allocation has an FX anchor.
    """
    db = _seed_fidelity_db(tmp_path, txns)
    qj = tmp_path / "qj.db"
    _init_qianji(qj, list(qianji_assets))
    with connected_db(db) as conn:
        for sym, day, close in extra_prices:
            conn.execute("INSERT INTO daily_close VALUES (?, ?, ?)", (sym, day, close))
        conn.execute("INSERT INTO daily_close VALUES ('CNY=X', '2025-01-02', 7.25)")
    return db, qj


# ── BUG 1: Cost basis wrong due to ORDER BY id ───────────────────────────


class TestCostBasisOrderedByDate:
    """:func:`etl.replay.replay_transactions` must produce correct cost basis
    regardless of insertion order.

    Root cause: ORDER BY id processes sells before buys when the buy was
    imported in a later CSV batch (higher id, earlier date).
    """

    def test_sell_after_buy_in_id_order_but_before_in_date_order(self, tmp_path: Path) -> None:
        # id=1 SELL on 01/10, id=2 BUY on 01/02: out-of-order ids must not
        # break cost-basis math.
        db = _seed_fidelity_db(tmp_path, [
            ("2025-01-10", "Z123", "YOU SOLD STOCK", "AAPL", -5, 3000),
            ("2025-01-02", "Z123", "YOU BOUGHT STOCK", "AAPL", 10, -5000),
        ])
        qty, cb = _replay_fidelity(db, date(2025, 1, 15))[("Z123", "AAPL")]
        assert qty == pytest.approx(5.0)
        # Cost basis: bought $5000 for 10 shares, sold 5/10 = 50% → CB = $2500.
        assert cb == pytest.approx(2500.0)

    def test_full_sell_zeroes_cost_basis_regardless_of_id_order(self, tmp_path: Path) -> None:
        db = _seed_fidelity_db(tmp_path, [
            ("2025-01-10", "Z123", "YOU SOLD ALL", "VOO", -10, 6000),
            ("2025-01-02", "Z123", "YOU BOUGHT X", "VOO", 10, -5000),
        ])
        # Position fully sold → not in positions (qty < 0.001 threshold).
        assert ("Z123", "VOO") not in _replay_fidelity(db, date(2025, 1, 15))

    def test_multiple_buys_then_sell_out_of_id_order(self, tmp_path: Path) -> None:
        db = _seed_fidelity_db(tmp_path, [
            ("2025-03-01", "Z123", "YOU SOLD X", "TSLA", -5, 2500),
            ("2025-02-01", "Z123", "YOU BOUGHT X", "TSLA", 10, -4000),
            ("2025-04-01", "Z123", "YOU BOUGHT X", "TSLA", 5, -2000),
        ])
        qty, cb = _replay_fidelity(db, date(2025, 4, 15))[("Z123", "TSLA")]
        # Chronological: buy 10 ($4000), sell 5 (50% → CB -= $2000 → $2000), buy 5 ($2000) → $4000.
        assert qty == pytest.approx(10.0)
        assert cb == pytest.approx(4000.0)


# ── BUG 2: first_held date wrong due to ORDER BY id ──────────────────────


class TestHoldingPeriodIsEarliestDate:
    """symbol_holding_periods_from_db must return the chronologically earliest
    transaction date, not the lowest-id transaction date.
    """

    def test_first_held_is_earliest_date_not_lowest_id(self, tmp_path: Path) -> None:
        # id=1 reinvestment on 12/24 (low id, late date) vs id=2 buy on 11/04.
        db = _seed_fidelity_db(tmp_path, [
            ("2025-12-24", "Z123", "REINVESTMENT", "SGOV", 0.5, -50),
            ("2025-11-04", "Z123", "YOU BOUGHT X", "SGOV", 240, -24096),
        ])
        first_held, _ = symbol_holding_periods_from_db(db)["SGOV"]
        assert first_held == date(2025, 11, 4)  # NOT 2025-12-24

    def test_multiple_symbols_all_correct(self, tmp_path: Path) -> None:
        db = _seed_fidelity_db(tmp_path, [
            ("2025-12-22", "Z123", "REINVESTMENT", "VTEB", 0.6, -30),       # VTEB late (low id)
            ("2025-12-29", "Z123", "YOU BOUGHT X", "GLDM", 4, -340),        # GLDM late
            ("2024-06-17", "Z123", "YOU BOUGHT X", "VOO", 1, -500),         # VOO single txn
            ("2025-12-04", "Z123", "YOU BOUGHT X", "VTEB", 219, -11000),    # VTEB early
            ("2025-10-29", "Z123", "YOU BOUGHT X", "GLDM", 12, -950),       # GLDM early
        ])
        periods = symbol_holding_periods_from_db(db)
        assert periods["VTEB"][0] == date(2025, 12, 4)
        assert periods["GLDM"][0] == date(2025, 10, 29)
        assert periods["VOO"][0] == date(2024, 6, 17)


# ── BUG 4: Positions without prices silently dropped ─────────────────────


class TestMissingPriceWarns:
    """When a Fidelity position has shares but no price data, the pipeline
    must log a warning instead of silently dropping the value.

    The warning is now emitted by ``etl.sources.fidelity`` rather than
    ``etl.allocation`` — watch the root WARNING level so either location is
    caught.
    """

    def test_warns_on_position_without_price(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        # Buy SGOV but only price VTI — SGOV must trigger the missing-price warning.
        db, qj = _allocation_setup(
            tmp_path,
            txns=[("2025-01-02", "Z123", "YOU BOUGHT X", "SGOV", 240, -24096)],
            extra_prices=[("VTI", "2025-01-02", 250.0)],
        )
        config = {
            "assets": {"SGOV": {"category": "Safe Net"}, "VTI": {"category": "US Equity"}},
            "qianji_accounts": {"fidelity_tracked": [], "ticker_map": {}},
        }
        with caplog.at_level(logging.WARNING):
            compute_daily_allocation(db, qj, config, date(2025, 1, 2), date(2025, 1, 2))
        sgov_warnings = [r for r in caplog.records if "SGOV" in r.message]
        assert len(sgov_warnings) > 0, "Expected a warning about SGOV missing price"

    def test_position_with_price_no_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        db, qj = _allocation_setup(
            tmp_path,
            txns=[("2025-01-02", "Z123", "YOU BOUGHT X", "VTI", 10, -2500)],
            extra_prices=[("VTI", "2025-01-02", 250.0)],
        )
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
    """A Qianji account with positive balance but no ticker_map entry must
    log a warning. Symmetric across USD and CNY.
    """

    @pytest.mark.parametrize(
        ("currency", "account", "expected_warn"),
        [
            ("USD", "Amex HYSA", True),
            ("CNY", "Alipay", True),
        ],
    )
    def test_unmapped_account_warns(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        currency: str,
        account: str,
        expected_warn: bool,
    ) -> None:
        # Need at least one Fidelity txn to establish a date range.
        db, qj = _allocation_setup(
            tmp_path,
            txns=[("2025-01-02", "Z123", "YOU BOUGHT X", "VTI", 10, -2500)],
            extra_prices=[("VTI", "2025-01-02", 250.0)],
            qianji_assets=[(account, 10000.0, currency)],
        )
        config = {
            "assets": {"VTI": {"category": "US Equity"}},
            "qianji_accounts": {"fidelity_tracked": [], "ticker_map": {}},
        }
        with caplog.at_level(logging.WARNING, logger="etl.allocation"):
            results = compute_daily_allocation(db, qj, config, date(2025, 1, 2), date(2025, 1, 2))
        unmapped = [r for r in caplog.records if account in r.message and "ticker_map" in r.message.lower()]
        assert (len(unmapped) > 0) is expected_warn
        # Unmapped accounts are excluded from allocation.
        tickers = {t["ticker"] for t in results[0]["tickers"]}
        assert account not in tickers

    def test_mapped_account_no_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        db, qj = _allocation_setup(
            tmp_path,
            txns=[("2025-01-02", "Z123", "YOU BOUGHT X", "VTI", 10, -2500)],
            extra_prices=[("VTI", "2025-01-02", 250.0)],
            qianji_assets=[("HYSA", 5000.0, "USD")],
        )
        config = {
            "assets": {"VTI": {"category": "US Equity"}, "HYSA": {"category": "Safe Net"}},
            "qianji_accounts": {"fidelity_tracked": [], "ticker_map": {"HYSA": "HYSA"}},
        }
        with caplog.at_level(logging.WARNING, logger="etl.allocation"):
            compute_daily_allocation(db, qj, config, date(2025, 1, 2), date(2025, 1, 2))
        unmapped = [r for r in caplog.records if "ticker_map" in r.message.lower() or "unmapped" in r.message.lower()]
        assert len(unmapped) == 0


# ── BUG 6: T-Bills (CUSIPs) have no price → missing from net worth ──────


class TestTBillCusipsValuedAtFace:
    """T-Bill positions use CUSIPs (e.g. 912797FY8) which have no yfinance
    price data. They should be valued at face value ($1/unit) and categorized
    as Safe Net.
    """

    @pytest.mark.parametrize(
        ("cusip", "qty", "amount"),
        [
            pytest.param("912796CR8", 3000, -2930, id="tbill-912"),
            pytest.param("46656MQ38", 4000, -4000, id="brokered-cd-non-912"),
        ],
    )
    def test_cusip_valued_at_face_value(
        self, tmp_path: Path, cusip: str, qty: int, amount: int,
    ) -> None:
        db, qj = _allocation_setup(
            tmp_path,
            txns=[("2025-01-02", "Z123", "YOU BOUGHT X", cusip, qty, amount)],
        )
        config = {
            "assets": {"T-Bills": {"category": "Safe Net"}},
            "qianji_accounts": {"fidelity_tracked": [], "ticker_map": {}},
        }
        results = compute_daily_allocation(db, qj, config, date(2025, 1, 2), date(2025, 1, 2))
        tickers = {t["ticker"]: t for t in results[0]["tickers"]}
        assert tickers["T-Bills"]["value"] == pytest.approx(float(qty))
        assert tickers["T-Bills"]["category"] == "Safe Net"

    def test_cusip_no_missing_price_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        db, qj = _allocation_setup(
            tmp_path,
            txns=[
                ("2025-01-02", "Z123", "YOU BOUGHT X", "912797FY8", 1000, -984),
                ("2025-01-02", "Z123", "YOU BOUGHT X", "06428FG68", 4000, -4000),
            ],
        )
        config = {
            "assets": {"T-Bills": {"category": "Safe Net"}},
            "qianji_accounts": {"fidelity_tracked": [], "ticker_map": {}},
        }
        with caplog.at_level(logging.WARNING, logger="etl.allocation"):
            compute_daily_allocation(db, qj, config, date(2025, 1, 2), date(2025, 1, 2))
        cusip_warnings = [r for r in caplog.records if "912797" in r.message or "06428" in r.message]
        assert len(cusip_warnings) == 0

    def test_multiple_cusips_aggregated(self, tmp_path: Path) -> None:
        db, qj = _allocation_setup(
            tmp_path,
            txns=[
                ("2025-01-02", "Z123", "YOU BOUGHT X", "912796CR8", 3000, -2930),
                ("2025-01-02", "Z123", "YOU BOUGHT X", "06428FG68", 4000, -4000),
            ],
        )
        config = {
            "assets": {"T-Bills": {"category": "Safe Net"}},
            "qianji_accounts": {"fidelity_tracked": [], "ticker_map": {}},
        }
        results = compute_daily_allocation(db, qj, config, date(2025, 1, 2), date(2025, 1, 2))
        tickers = {t["ticker"]: t for t in results[0]["tickers"]}
        assert tickers["T-Bills"]["value"] == pytest.approx(7000.0)
        assert results[0]["safe_net"] == pytest.approx(7000.0)
