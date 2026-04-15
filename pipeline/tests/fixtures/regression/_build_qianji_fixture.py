"""Deterministic builder for Qianji SQLite L2 fixture.

Committed so the binary fixture can be regenerated and reviewed. Mirrors
the columns the production queries read:

- ``user_asset`` needs ``name, money, currency, status`` (real rows use
  ``status=0``).
- ``user_bill`` needs ``id, type, money, fromact, targetact, remark, time,
  cateid, extra, status`` (real rows use ``status=1``).
- ``category`` needs ``id, name`` for the cat-id → name mapping.

All timestamps are frozen inside 2024 so the L2 build window is stable.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

OUT = Path(__file__).parent / "qianji.sqlite"

# Unix timestamps (UTC) used by the fixture. Deliberately placed on
# weekdays so Qianji txn dates line up with the allocation loop's
# weekday-only iteration.
TS_2024_02_15 = 1707955200  # Thu 2024-02-15
TS_2024_03_15 = 1710460800  # Fri 2024-03-15
TS_2024_05_15 = 1715731200  # Wed 2024-05-15
TS_2024_07_15 = 1721001600  # Mon 2024-07-15
TS_2024_09_15 = 1726358400  # Sun — shift to Mon 2024-09-16
TS_2024_09_16 = 1726444800  # Mon 2024-09-16
TS_2024_11_15 = 1731628800  # Fri 2024-11-15


def main() -> None:
    if OUT.exists():
        OUT.unlink()
    conn = sqlite3.connect(OUT)
    conn.executescript(
        """
        CREATE TABLE user_asset (
            id INTEGER PRIMARY KEY,
            name TEXT,
            type INTEGER,
            money REAL,
            currency TEXT,
            status INTEGER
        );
        CREATE TABLE user_bill (
            id INTEGER PRIMARY KEY,
            type INTEGER,
            money REAL,
            time INTEGER,
            fromact TEXT,
            targetact TEXT,
            remark TEXT,
            cateid INTEGER,
            extra TEXT,
            status INTEGER
        );
        CREATE TABLE category (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        """
    )
    # status=0 means "visible/active" on user_asset.
    conn.executemany(
        "INSERT INTO user_asset (id, name, type, money, currency, status) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, "Chase Debit", 1, 2500.0, "USD", 0),
            (2, "建行卡", 1, 30000.0, "CNY", 0),
            (3, "CFF", 2, -500.0, "USD", 0),  # credit card (liability)
            (4, "Amex HYSA", 1, 10000.0, "USD", 0),
        ],
    )
    # status=1 means "active/kept" on user_bill.
    # type 0 = expense, 1 = income, 2 = transfer, 3 = repayment.
    conn.executemany(
        "INSERT INTO user_bill (id, type, money, time, fromact, targetact, remark, cateid, extra, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            # Income into Chase Debit (not a 401k row — that path is covered
            # by the QFX BUYMF contributions).
            (1, 1, 5000.0, TS_2024_02_15, "Chase Debit", "", "Salary", 200, "", 1),
            # Expense in CNY out of 建行卡.
            (2, 0, 200.0, TS_2024_03_15, "建行卡", "", "餐饮", 101, "", 1),
            # USD expense from Chase Debit.
            (3, 0, 75.0, TS_2024_05_15, "Chase Debit", "", "Groceries", 100, "", 1),
            # Transfer from Chase Debit to Amex HYSA (intra-USD).
            (4, 2, 1000.0, TS_2024_07_15, "Chase Debit", "Amex HYSA", "Savings", 300, "", 1),
            # Credit card repayment: Chase Debit pays off CFF.
            (5, 3, 250.0, TS_2024_09_16, "Chase Debit", "CFF", "CC payment", 301, "", 1),
            # Another income row late in the year.
            (6, 1, 4500.0, TS_2024_11_15, "Chase Debit", "", "Bonus", 201, "", 1),
        ],
    )
    conn.executemany(
        "INSERT INTO category (id, name) VALUES (?, ?)",
        [
            (100, "Groceries"),
            (101, "餐饮"),
            (200, "Salary"),
            (201, "Bonus"),
            (300, "Transfer"),
            (301, "CC Payment"),
        ],
    )
    conn.commit()
    conn.close()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
