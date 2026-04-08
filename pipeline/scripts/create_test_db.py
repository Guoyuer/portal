"""Create a test DB for E2E tests (CI or local)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generate_asset_snapshot.db import get_connection, init_db

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "timemachine.db"


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    init_db(DB_PATH)
    conn = get_connection(DB_PATH)

    # Daily portfolio values (3 months of data)
    days = [
        ("2025-01-02", 100000, 55000, 15000, 3000, 27000, -500),
        ("2025-02-03", 110000, 60500, 16500, 3300, 29700, -400),
        ("2025-03-03", 120000, 66000, 18000, 3600, 32400, -300),
        ("2025-04-01", 130000, 71500, 19500, 3900, 35100, -200),
    ]
    conn.executemany(
        "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net, liabilities) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        days,
    )

    # Prefix sums
    conn.executemany(
        "INSERT INTO computed_prefix (date, income, expenses, buys, sells, dividends, net_cash_in, cc_payments) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("2025-01-02", 5000, 1000, 3000, 0, 10, 2000, 500),
            ("2025-02-03", 10000, 2000, 6000, 0, 25, 4000, 1000),
            ("2025-03-03", 15000, 3000, 9000, 0, 40, 6000, 1500),
            ("2025-04-01", 20000, 4000, 12000, 0, 55, 8000, 2000),
        ],
    )

    # Ticker-level data for each day
    tickers_template = [
        ("VOO", "US Equity", "broad", 0.4),
        ("QQQM", "US Equity", "growth", 0.15),
        ("VXUS", "Non-US Equity", "broad", 0.15),
        ("BTC", "Crypto", "", 0.03),
        ("FZFXX", "Safe Net", "", 0.27),
    ]
    for date, total, *_ in days:
        for ticker, cat, sub, frac in tickers_template:
            val = round(total * frac, 2)
            conn.execute(
                "INSERT INTO computed_daily_tickers (date, ticker, value, category, subtype, cost_basis, gain_loss, gain_loss_pct)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (date, ticker, val, cat, sub, round(val * 0.8, 2), round(val * 0.2, 2), 25.0),
            )

    # Fidelity transactions
    conn.executemany(
        "INSERT INTO fidelity_transactions (run_date, account, account_number, action, symbol, description, lot_type, quantity, price, amount)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("01/02/2025", "Taxable", "Z00", "YOU BOUGHT VANGUARD S&P 500 ETF", "VOO", "", "Cash", 2, 500, -1000),
            ("01/02/2025", "Taxable", "Z00", "YOU BOUGHT INVESCO QQQ TRUST", "QQQM", "", "Cash", 5, 200, -1000),
            ("01/02/2025", "Taxable", "Z00", "DIVIDEND RECEIVED", "VOO", "", "Cash", 0, 0, 10),
            ("02/03/2025", "Taxable", "Z00", "YOU BOUGHT VANGUARD S&P 500 ETF", "VOO", "", "Cash", 3, 510, -1530),
            ("03/03/2025", "Taxable", "Z00", "YOU SOLD INVESCO QQQ TRUST", "QQQM", "", "Cash", -2, 210, 420),
            ("03/03/2025", "Taxable", "Z00", "DIVIDEND RECEIVED", "VXUS", "", "Cash", 0, 0, 15),
        ],
    )

    # Qianji transactions
    conn.executemany(
        "INSERT INTO qianji_transactions (date, type, category, amount, account, note) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("2025-01-15", "income", "Salary", 5000, "Checking", ""),
            ("2025-01-15", "income", "401K", 1000, "401K", ""),
            ("2025-01-20", "expense", "Housing", 1500, "Checking", ""),
            ("2025-01-25", "expense", "Meals", 200, "Credit Card", ""),
            ("2025-02-15", "income", "Salary", 5000, "Checking", ""),
            ("2025-02-15", "income", "401K", 1000, "401K", ""),
            ("2025-02-20", "expense", "Housing", 1500, "Checking", ""),
            ("2025-02-25", "expense", "Meals", 180, "Credit Card", ""),
            ("2025-02-28", "repayment", "Credit Card", 380, "Checking", ""),
            ("2025-03-15", "income", "Salary", 5000, "Checking", ""),
            ("2025-03-20", "expense", "Housing", 1500, "Checking", ""),
            ("2025-03-25", "expense", "Meals", 250, "Credit Card", ""),
        ],
    )

    # Market data (index prices)
    conn.executemany(
        "INSERT INTO daily_close (date, symbol, close) VALUES (?, ?, ?)",
        [
            ("2025-01-02", "^GSPC", 5900),
            ("2025-02-03", "^GSPC", 6000),
            ("2025-03-03", "^GSPC", 5800),
            ("2025-04-01", "^GSPC", 6100),
            ("2025-01-02", "^NDX", 21000),
            ("2025-02-03", "^NDX", 21500),
            ("2025-03-03", "^NDX", 20800),
            ("2025-04-01", "^NDX", 22000),
            ("2025-01-02", "CNY=X", 7.25),
            ("2025-04-01", "CNY=X", 7.28),
        ],
    )

    conn.commit()
    conn.close()
    print(f"Created test DB at {DB_PATH}")


if __name__ == "__main__":
    main()
