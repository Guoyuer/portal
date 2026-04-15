"""Deterministic builder for Qianji SQLite L2 fixture.
Committed so the binary fixture can be regenerated and reviewed."""
from __future__ import annotations

import sqlite3
from pathlib import Path

OUT = Path(__file__).parent / "qianji.sqlite"


def main() -> None:
    if OUT.exists():
        OUT.unlink()
    conn = sqlite3.connect(OUT)
    conn.executescript(
        """
        CREATE TABLE user_asset (
            id INTEGER PRIMARY KEY, name TEXT, type INTEGER, money REAL, currency TEXT
        );
        CREATE TABLE user_bill (
            id INTEGER PRIMARY KEY, type INTEGER, money REAL, time INTEGER,
            assetid INTEGER, categoryid INTEGER, remark TEXT
        );
        INSERT INTO user_asset VALUES
            (1, 'US Checking', 1, 5000.0, 'USD'),
            (2, 'CNY Savings', 1, 30000.0, 'CNY'),
            (3, 'Credit Card', 2, -500.0, 'USD');
        INSERT INTO user_bill VALUES
            (1, 0, 50.0, 1704096000, 1, 100, 'Groceries'),
            (2, 0, 200.0, 1705305600, 2, 101, '餐饮'),
            (3, 1, 2000.0, 1706515200, 1, 200, 'Salary');
        """
    )
    conn.commit()
    conn.close()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
