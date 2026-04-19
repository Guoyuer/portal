"""Shared INSERT helpers for ``test_replay_primitive`` / ``_properties`` /
``test_bug_fixes``. Leading underscore keeps pytest from collecting it."""
from __future__ import annotations

import sqlite3


def insert_fidelity_txn(
    conn: sqlite3.Connection,
    *,
    run_date: str,
    account_number: str,
    action: str,
    action_kind: str,
    symbol: str = "",
    lot_type: str = "",
    quantity: float = 0.0,
    amount: float = 0.0,
    action_type: str = "",
    price: float = 0.0,
) -> None:
    """Insert one ``fidelity_transactions`` row — superset of columns the
    three consumers previously set via parallel helpers."""
    conn.execute(
        "INSERT INTO fidelity_transactions "
        "(run_date, account_number, action, action_type, action_kind, symbol, "
        "lot_type, quantity, price, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_date, account_number, action, action_type, action_kind,
         symbol, lot_type, quantity, price, amount),
    )


def insert_prop_rows(
    db_path,
    rows: list[tuple[str, str, str, float, float]],
) -> None:
    """Insert ``(txn_date, action_kind, ticker, quantity, amount_usd)`` rows
    into the Robinhood-shaped ``prop_transactions`` table used by hypothesis
    property tests."""
    conn = sqlite3.connect(str(db_path))
    conn.executemany(
        "INSERT INTO prop_transactions "
        "(txn_date, action_kind, ticker, quantity, amount_usd) VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
