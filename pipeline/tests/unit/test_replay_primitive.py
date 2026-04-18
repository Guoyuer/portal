"""Unit tests for the source-agnostic replay primitive."""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from etl.replay import PositionState, ReplayConfig, ReplayResult, replay_transactions  # noqa: F401
from etl.sources import ActionKind

# ── Robinhood-shaped table (no account, tx_date column) ─────────────────────


MINI_REPLAY = ReplayConfig(table="mini_transactions")


@pytest.fixture
def mini_db(tmp_path: Path) -> Path:
    """Create a tiny SQLite DB with a normalized transactions table."""
    db = tmp_path / "mini.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE mini_transactions (
            id INTEGER PRIMARY KEY,
            txn_date TEXT NOT NULL,
            action_kind TEXT NOT NULL,
            account TEXT,
            ticker TEXT NOT NULL,
            quantity REAL NOT NULL,
            amount_usd REAL NOT NULL
        );
        """
    )
    conn.executemany(
        "INSERT INTO mini_transactions (txn_date, action_kind, account, ticker, quantity, amount_usd) "
        "VALUES (?,?,?,?,?,?)",
        [
            ("2024-01-02", ActionKind.BUY.value, "A1", "FOO", 10.0, -1000.0),
            ("2024-01-03", ActionKind.BUY.value, "A1", "FOO", 5.0, -550.0),
            ("2024-02-01", ActionKind.SELL.value, "A1", "FOO", -3.0, 330.0),
            ("2024-03-01", ActionKind.DIVIDEND.value, "A1", "FOO", 0.0, 12.0),
        ],
    )
    conn.commit()
    conn.close()
    return db


def test_replay_accumulates_position_and_cost_basis(mini_db: Path) -> None:
    result = replay_transactions(mini_db, MINI_REPLAY, date(2024, 2, 15))
    assert set(result.positions.keys()) == {("", "FOO")}
    foo = result.positions[("", "FOO")]
    assert foo.quantity == pytest.approx(12.0)  # 10 + 5 - 3
    # Cost basis reduced proportionally on sell: 1550 * (1 - 3/15) = 1240
    assert foo.cost_basis_usd == pytest.approx(1240.0, rel=1e-3)


def test_replay_respects_as_of_cutoff(mini_db: Path) -> None:
    result = replay_transactions(mini_db, MINI_REPLAY, date(2024, 1, 2))
    foo = result.positions[("", "FOO")]
    assert foo.quantity == pytest.approx(10.0)
    assert foo.cost_basis_usd == pytest.approx(1000.0)


def test_replay_dropped_zero_positions(mini_db: Path) -> None:
    """Fully sold-out tickers shouldn't appear in the result."""
    conn = sqlite3.connect(str(mini_db))
    conn.executemany(
        "INSERT INTO mini_transactions (txn_date, action_kind, account, ticker, quantity, amount_usd) "
        "VALUES (?,?,?,?,?,?)",
        [
            ("2024-01-10", ActionKind.BUY.value, "A1", "BAR", 5.0, -200.0),
            ("2024-01-20", ActionKind.SELL.value, "A1", "BAR", -5.0, 220.0),
        ],
    )
    conn.commit()
    conn.close()
    result = replay_transactions(mini_db, MINI_REPLAY, date(2024, 2, 15))
    assert ("", "BAR") not in result.positions


# ── Fidelity-shaped table (per-account, cash, Type=Shares filter) ───────────


FIDELITY_BASIC_REPLAY = ReplayConfig(
    table="fidelity_transactions",
    date_col="run_date",
    ticker_col="symbol",
    amount_col="amount",
    account_col="account_number",
)

FIDELITY_WITH_CASH_REPLAY = ReplayConfig(
    table="fidelity_transactions",
    date_col="run_date",
    ticker_col="symbol",
    amount_col="amount",
    account_col="account_number",
    track_cash=True,
    lot_type_col="lot_type",
)


@pytest.fixture
def fidelity_like_db(tmp_path: Path) -> Path:
    """Create a Fidelity-shaped transactions table with the vocabulary that
    exercises ``replay_transactions``' widened behaviour (REDEMPTION /
    DISTRIBUTION / EXCHANGE / TRANSFER plus cash tracking with ``Type=Shares``
    exclusion)."""
    db = tmp_path / "fid.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE fidelity_transactions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date        TEXT NOT NULL,
            account_number  TEXT NOT NULL,
            action          TEXT NOT NULL,
            action_kind     TEXT NOT NULL,
            symbol          TEXT NOT NULL,
            lot_type        TEXT NOT NULL,
            quantity        REAL NOT NULL,
            amount          REAL NOT NULL
        );
        """
    )
    return db


def _insert(conn: sqlite3.Connection, run_date: str, acct: str, kind: ActionKind,
            symbol: str, lot_type: str, qty: float, amt: float,
            action: str = "") -> None:
    conn.execute(
        "INSERT INTO fidelity_transactions "
        "(run_date, account_number, action, action_kind, symbol, lot_type, quantity, amount) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (run_date, acct, action or kind.value, kind.value, symbol, lot_type, qty, amt),
    )


def test_fidelity_per_account_keying(fidelity_like_db: Path) -> None:
    """Same ticker in two accounts stays split."""
    conn = sqlite3.connect(str(fidelity_like_db))
    _insert(conn, "2024-01-02", "Z001", ActionKind.BUY, "VTI", "Cash", 10, -2000)
    _insert(conn, "2024-01-02", "Z002", ActionKind.BUY, "VTI", "Cash", 5, -1000)
    conn.commit()
    conn.close()

    result = replay_transactions(fidelity_like_db, FIDELITY_BASIC_REPLAY, date(2024, 2, 1))

    assert result.positions[("Z001", "VTI")].quantity == pytest.approx(10.0)
    assert result.positions[("Z002", "VTI")].quantity == pytest.approx(5.0)


def test_fidelity_redemption_qty_only(fidelity_like_db: Path) -> None:
    """REDEMPTION updates qty without touching cost basis — legacy
    ``POSITION_PREFIXES`` semantics."""
    conn = sqlite3.connect(str(fidelity_like_db))
    _insert(conn, "2024-01-02", "Z001", ActionKind.BUY, "CUSIP", "Cash", 1000, -990)
    _insert(conn, "2024-06-15", "Z001", ActionKind.REDEMPTION, "CUSIP", "Cash", -1000, 1000,
            action="REDEMPTION PAYOUT")
    conn.commit()
    conn.close()

    result = replay_transactions(fidelity_like_db, FIDELITY_BASIC_REPLAY, date(2024, 12, 31))
    # Full redemption wipes the position, keeps cost_basis at the original BUY.
    assert ("Z001", "CUSIP") not in result.positions


def test_fidelity_distribution_qty_only(fidelity_like_db: Path) -> None:
    """DISTRIBUTION adds shares without touching cost basis."""
    conn = sqlite3.connect(str(fidelity_like_db))
    _insert(conn, "2024-01-02", "Z001", ActionKind.BUY, "AVGO", "Cash", 10, -1000)
    _insert(conn, "2024-07-15", "Z001", ActionKind.DISTRIBUTION, "AVGO", "Shares", 9.063, 1553.57,
            action="DISTRIBUTION BROADCOM INC")
    conn.commit()
    conn.close()

    result = replay_transactions(fidelity_like_db, FIDELITY_BASIC_REPLAY, date(2024, 12, 31))
    avgo = result.positions[("Z001", "AVGO")]
    assert avgo.quantity == pytest.approx(19.063)
    assert avgo.cost_basis_usd == pytest.approx(1000.0)  # unchanged by distribution


def test_fidelity_transfer_qty_only(fidelity_like_db: Path) -> None:
    """TRANSFERRED FROM / TO both treat qty as the delta (no cost basis)."""
    conn = sqlite3.connect(str(fidelity_like_db))
    _insert(conn, "2024-01-02", "Z001", ActionKind.BUY, "VTI", "Cash", 10, -2000)
    _insert(conn, "2024-02-01", "Z001", ActionKind.TRANSFER, "VTI", "Shares", -4, -800,
            action="TRANSFERRED FROM Z001")
    _insert(conn, "2024-02-01", "Z002", ActionKind.TRANSFER, "VTI", "Shares", 4, 800,
            action="TRANSFERRED TO Z002")
    conn.commit()
    conn.close()

    result = replay_transactions(fidelity_like_db, FIDELITY_BASIC_REPLAY, date(2024, 12, 31))
    assert result.positions[("Z001", "VTI")].quantity == pytest.approx(6.0)
    assert result.positions[("Z002", "VTI")].quantity == pytest.approx(4.0)


def test_fidelity_cash_tracking_with_shares_exclusion(fidelity_like_db: Path) -> None:
    """``Type=Shares`` rows must NOT feed the cash ledger."""
    conn = sqlite3.connect(str(fidelity_like_db))
    _insert(conn, "2024-01-02", "Z001", ActionKind.DEPOSIT, "", "Cash", 0, 5000,
            action="Electronic Funds Transfer Received")
    _insert(conn, "2024-01-03", "Z001", ActionKind.BUY, "VTI", "Cash", 10, -2000)
    # A Type=Shares DISTRIBUTION with positive amt must NOT increment cash.
    _insert(conn, "2024-07-15", "Z001", ActionKind.DISTRIBUTION, "AVGO", "Shares", 9.063, 1553.57)
    conn.commit()
    conn.close()

    result = replay_transactions(fidelity_like_db, FIDELITY_WITH_CASH_REPLAY, date(2024, 12, 31))
    # 5000 (deposit) + (-2000) (buy) = 3000. The Type=Shares 1553.57 is excluded.
    assert result.cash["Z001"] == pytest.approx(3000.0)


def test_fidelity_mm_symbols_excluded_from_positions(fidelity_like_db: Path) -> None:
    """Tickers in ``exclude_tickers`` never accumulate shares."""
    conn = sqlite3.connect(str(fidelity_like_db))
    _insert(conn, "2024-01-02", "Z001", ActionKind.REINVESTMENT, "SPAXX", "Cash", 100, -100)
    conn.commit()
    conn.close()

    cfg = ReplayConfig(
        table="fidelity_transactions",
        date_col="run_date", ticker_col="symbol", amount_col="amount",
        account_col="account_number",
        exclude_tickers=frozenset({"SPAXX"}),
    )
    result = replay_transactions(fidelity_like_db, cfg, date(2024, 12, 31))
    assert ("Z001", "SPAXX") not in result.positions


def test_fidelity_mm_drip_adds_to_cash(fidelity_like_db: Path) -> None:
    """MM REINVESTMENT adds the qty (shares @ $1) to the account's cash."""
    conn = sqlite3.connect(str(fidelity_like_db))
    _insert(conn, "2024-01-02", "Z001", ActionKind.DEPOSIT, "", "Cash", 0, 1000,
            action="Electronic Funds Transfer Received")
    # MM DRIP: REINVESTMENT of SPAXX with qty=5, amt=0 (shares credited w/o cash flow)
    _insert(conn, "2024-02-01", "Z001", ActionKind.REINVESTMENT, "SPAXX", "Cash", 5, 0,
            action="REINVESTMENT SPAXX")
    conn.commit()
    conn.close()

    cfg = ReplayConfig(
        table="fidelity_transactions",
        date_col="run_date", ticker_col="symbol", amount_col="amount",
        account_col="account_number",
        track_cash=True, lot_type_col="lot_type",
        exclude_tickers=frozenset({"SPAXX"}),
        mm_drip_tickers=frozenset({"SPAXX"}),
    )
    result = replay_transactions(fidelity_like_db, cfg, date(2024, 12, 31))
    # 1000 deposit + 5 DRIP shares at $1 each = 1005
    assert result.cash["Z001"] == pytest.approx(1005.0)


def test_fidelity_cash_account_regex_filter(fidelity_like_db: Path) -> None:
    """Cash is only kept for uppercase-alphanumeric account numbers — matches
    legacy ``[A-Z0-9]+`` filter that drops UUID / lowercase internal accounts.
    """
    conn = sqlite3.connect(str(fidelity_like_db))
    _insert(conn, "2024-01-02", "Z001", ActionKind.DEPOSIT, "", "Cash", 0, 1000)
    _insert(conn, "2024-01-02", "2ad9d14c-xxx", ActionKind.DEPOSIT, "", "Cash", 0, 500)
    conn.commit()
    conn.close()

    result = replay_transactions(fidelity_like_db, FIDELITY_WITH_CASH_REPLAY, date(2024, 12, 31))
    assert "Z001" in result.cash
    assert "2ad9d14c-xxx" not in result.cash


def test_fidelity_sell_without_prior_holdings(fidelity_like_db: Path) -> None:
    """Matches legacy: a SELL with no prior BUY leaves cost basis at 0
    but applies qty (goes negative). Rarely hits production but the parity
    check matters."""
    conn = sqlite3.connect(str(fidelity_like_db))
    _insert(conn, "2024-01-02", "Z001", ActionKind.SELL, "ORPHAN", "Cash", -3, 300)
    conn.commit()
    conn.close()

    result = replay_transactions(fidelity_like_db, FIDELITY_BASIC_REPLAY, date(2024, 12, 31))
    orphan = result.positions[("Z001", "ORPHAN")]
    assert orphan.quantity == pytest.approx(-3.0)
    assert orphan.cost_basis_usd == pytest.approx(0.0)
