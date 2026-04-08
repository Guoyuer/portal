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
        "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net) "
        "VALUES ('2025-01-02', 100000, 55000, 15000, 3000, 27000)"
    )
    conn.execute(
        "INSERT INTO computed_prefix (date, income, expenses, buys, sells, dividends, net_cash_in, cc_payments) "
        "VALUES ('2025-01-02', 5000, 1000, 3000, 0, 10, 2000, 500)"
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

    def test_missing_date(self, client: TestClient) -> None:
        resp = client.get("/allocation", params={"date": "1999-01-01"})
        assert resp.status_code == 200
        assert resp.json()["categories"] == []
