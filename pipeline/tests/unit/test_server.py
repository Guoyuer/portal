"""Tests for FastAPI timemachine server."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from generate_asset_snapshot.db import get_connection, init_db


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    init_db(p)
    conn = get_connection(p)
    conn.execute(
        "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net, liabilities) "
        "VALUES ('2025-01-02', 100000, 55000, 15000, 3000, 27000, -500)"
    )
    conn.execute(
        "INSERT INTO computed_prefix (date, income, expenses, buys, sells, dividends, net_cash_in, cc_payments) "
        "VALUES ('2025-01-02', 5000, 1000, 3000, 0, 10, 2000, 500)"
    )
    # Ticker-level data
    conn.executemany(
        "INSERT INTO computed_daily_tickers (date, ticker, value, category, subtype, cost_basis, gain_loss, gain_loss_pct)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("2025-01-02", "VOO", 40000, "US Equity", "broad", 30000, 10000, 33.3),
            ("2025-01-02", "QQQM", 15000, "US Equity", "growth", 12000, 3000, 25.0),
            ("2025-01-02", "VXUS", 15000, "Non-US Equity", "broad", 16000, -1000, -6.25),
            ("2025-01-02", "BTC", 3000, "Crypto", "", 2000, 1000, 50.0),
            ("2025-01-02", "FZFXX", 27000, "Safe Net", "", 27000, 0, 0),
        ],
    )
    # Fidelity transactions for activity endpoint
    conn.executemany(
        "INSERT INTO fidelity_transactions (run_date, account, account_number, action, symbol, description, lot_type, quantity, price, amount)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("01/02/2025", "Taxable", "Z29133576", "YOU BOUGHT VANGUARD S&P 500 ETF", "VOO", "", "Cash", 2, 500.0, -1000.0),
            ("01/02/2025", "Taxable", "Z29133576", "YOU BOUGHT INVESCO QQQ TRUST", "QQQM", "", "Cash", 5, 200.0, -1000.0),
            ("01/02/2025", "Taxable", "Z29133576", "DIVIDEND RECEIVED", "VOO", "", "Cash", 0, 0, 10.0),
            ("01/02/2025", "Taxable", "Z29133576", "DIVIDEND RECEIVED", "QQQM", "", "Cash", 0, 0, 5.0),
            ("01/15/2025", "Taxable", "Z29133576", "YOU BOUGHT VANGUARD S&P 500 ETF", "VOO", "", "Cash", 3, 510.0, -1530.0),
            ("02/01/2025", "Taxable", "Z29133576", "YOU SOLD INVESCO QQQ TRUST", "QQQM", "", "Cash", -2, 210.0, 420.0),
        ],
    )
    conn.commit()
    conn.close()
    return p


@pytest.fixture()
def client(db_path: Path) -> TestClient:
    from generate_asset_snapshot.server import create_app

    app = create_app(db_path)
    return TestClient(app)


class TestTimeline:
    def test_returns_daily_and_prefix(self, client: TestClient) -> None:
        resp = client.get("/timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert "daily" in data
        assert "prefix" in data
        assert len(data["daily"]) == 1
        assert data["daily"][0]["total"] == 100000
        assert data["daily"][0]["usEquity"] == 55000

    def test_prefix_keys_camel_case(self, client: TestClient) -> None:
        data = client.get("/timeline").json()
        p = data["prefix"][0]
        assert "netCashIn" in p
        assert "ccPayments" in p

    def test_empty_db(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.db"
        init_db(p)
        from generate_asset_snapshot.server import create_app

        c = TestClient(create_app(p))
        resp = c.get("/timeline")
        assert resp.status_code == 200
        assert resp.json() == {"daily": [], "prefix": []}


class TestAllocation:
    def test_returns_categories(self, client: TestClient) -> None:
        resp = client.get("/allocation", params={"date": "2025-01-02"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 100000
        assert len(data["categories"]) == 4
        us = next(c for c in data["categories"] if c["name"] == "US Equity")
        assert us["pct"] == 55.0
        assert us["deviation"] == 0.0

    def test_returns_liabilities_and_net_worth(self, client: TestClient) -> None:
        resp = client.get("/allocation", params={"date": "2025-01-02"})
        data = resp.json()
        assert data["liabilities"] == -500
        assert data["netWorth"] == 99500  # 100000 + (-500)

    def test_returns_tickers(self, client: TestClient) -> None:
        resp = client.get("/allocation", params={"date": "2025-01-02"})
        data = resp.json()
        assert "tickers" in data
        assert len(data["tickers"]) == 5
        voo = next(t for t in data["tickers"] if t["ticker"] == "VOO")
        assert voo["value"] == 40000
        assert voo["category"] == "US Equity"
        assert voo["subtype"] == "broad"
        assert voo["costBasis"] == 30000
        assert voo["gainLoss"] == 10000
        assert voo["gainLossPct"] == 33.3

    def test_tickers_sorted_by_value_desc(self, client: TestClient) -> None:
        resp = client.get("/allocation", params={"date": "2025-01-02"})
        tickers = resp.json()["tickers"]
        values = [t["value"] for t in tickers]
        assert values == sorted(values, reverse=True)

    def test_missing_date(self, client: TestClient) -> None:
        resp = client.get("/allocation", params={"date": "1999-01-01"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["categories"] == []
        assert data["tickers"] == []


class TestActivity:
    def test_returns_buys_and_dividends(self, client: TestClient) -> None:
        resp = client.get("/activity", params={"start": "2025-01-01", "end": "2025-01-31"})
        assert resp.status_code == 200
        data = resp.json()
        assert "buysBySymbol" in data
        assert "dividendsBySymbol" in data
        # 2 buys for VOO + 1 buy for QQQM in January
        voo_buy = next(b for b in data["buysBySymbol"] if b["symbol"] == "VOO")
        assert voo_buy["count"] == 2
        assert voo_buy["total"] == 2530.0  # 1000 + 1530
        qqqm_buy = next(b for b in data["buysBySymbol"] if b["symbol"] == "QQQM")
        assert qqqm_buy["count"] == 1
        assert qqqm_buy["total"] == 1000.0

    def test_dividends_grouped(self, client: TestClient) -> None:
        resp = client.get("/activity", params={"start": "2025-01-01", "end": "2025-01-31"})
        data = resp.json()
        voo_div = next(d for d in data["dividendsBySymbol"] if d["symbol"] == "VOO")
        assert voo_div["total"] == 10.0
        qqqm_div = next(d for d in data["dividendsBySymbol"] if d["symbol"] == "QQQM")
        assert qqqm_div["total"] == 5.0

    def test_date_range_filters(self, client: TestClient) -> None:
        # February only — should only see the QQQM sell (not returned as buy)
        resp = client.get("/activity", params={"start": "2025-02-01", "end": "2025-02-28"})
        data = resp.json()
        assert len(data["buysBySymbol"]) == 0
        assert len(data["dividendsBySymbol"]) == 0

    def test_sells_returned(self, client: TestClient) -> None:
        resp = client.get("/activity", params={"start": "2025-02-01", "end": "2025-02-28"})
        data = resp.json()
        assert "sellsBySymbol" in data
        qqqm_sell = next(s for s in data["sellsBySymbol"] if s["symbol"] == "QQQM")
        assert qqqm_sell["total"] == 420.0

    def test_empty_range(self, client: TestClient) -> None:
        resp = client.get("/activity", params={"start": "2024-01-01", "end": "2024-01-31"})
        data = resp.json()
        assert data["buysBySymbol"] == []
        assert data["dividendsBySymbol"] == []
        assert data["sellsBySymbol"] == []

    def test_sorted_by_total_desc(self, client: TestClient) -> None:
        resp = client.get("/activity", params={"start": "2025-01-01", "end": "2025-01-31"})
        buys = resp.json()["buysBySymbol"]
        totals = [b["total"] for b in buys]
        assert totals == sorted(totals, reverse=True)
