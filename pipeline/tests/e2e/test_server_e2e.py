"""E2E tests for the timemachine FastAPI server against real data.

Requires: timemachine.db at pipeline/data/timemachine.db (built via build_timemachine_db.py).
Skips automatically if DB is missing.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "timemachine.db"


@pytest.fixture(scope="module")
def client() -> TestClient:
    if not DB_PATH.exists():
        pytest.skip("timemachine.db not found — run build_timemachine_db.py first")
    from generate_asset_snapshot.server import create_app
    return TestClient(create_app(DB_PATH))


# ── /timeline ──────────────────────────────────────────────────────────────


class TestTimelineE2E:
    def test_returns_non_empty(self, client: TestClient) -> None:
        resp = client.get("/timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["daily"]) > 500
        assert len(data["prefix"]) > 500

    def test_daily_has_required_fields(self, client: TestClient) -> None:
        day = client.get("/timeline").json()["daily"][-1]
        for key in ("date", "total", "usEquity", "nonUsEquity", "crypto", "safeNet"):
            assert key in day, f"Missing key: {key}"
        assert day["total"] > 0

    def test_prefix_has_required_fields(self, client: TestClient) -> None:
        pf = client.get("/timeline").json()["prefix"][-1]
        for key in ("date", "income", "expenses", "buys", "sells", "dividends", "netCashIn", "ccPayments"):
            assert key in pf, f"Missing key: {key}"

    def test_daily_sorted_by_date(self, client: TestClient) -> None:
        dates = [d["date"] for d in client.get("/timeline").json()["daily"]]
        assert dates == sorted(dates)

    def test_category_sum_matches_total(self, client: TestClient) -> None:
        day = client.get("/timeline").json()["daily"][-1]
        cat_sum = day["usEquity"] + day["nonUsEquity"] + day["crypto"] + day["safeNet"]
        assert abs(cat_sum - day["total"]) < 1.0, f"Category sum {cat_sum} != total {day['total']}"


# ── /allocation ────────────────────────────────────────────────────────────


class TestAllocationE2E:
    def test_latest_date(self, client: TestClient) -> None:
        resp = client.get("/allocation", params={"date": "2026-04-07"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 400000
        assert data["netWorth"] > 400000
        assert data["liabilities"] < 0

    def test_has_tickers(self, client: TestClient) -> None:
        data = client.get("/allocation", params={"date": "2026-04-07"}).json()
        assert len(data["tickers"]) > 30
        # Verify known tickers exist
        ticker_names = {t["ticker"] for t in data["tickers"]}
        assert "VOO" in ticker_names
        assert "QQQM" in ticker_names

    def test_ticker_fields_complete(self, client: TestClient) -> None:
        data = client.get("/allocation", params={"date": "2026-04-07"}).json()
        voo = next(t for t in data["tickers"] if t["ticker"] == "VOO")
        assert voo["value"] > 0
        assert voo["category"] == "US Equity"
        assert voo["subtype"] == "broad"
        assert voo["costBasis"] > 0
        assert voo["gainLoss"] != 0  # VOO should have some gain

    def test_cost_basis_sanity(self, client: TestClient) -> None:
        """Gain/loss should be consistent with value - costBasis."""
        data = client.get("/allocation", params={"date": "2026-04-07"}).json()
        for t in data["tickers"]:
            if t["costBasis"] > 0:
                expected_gl = round(t["value"] - t["costBasis"], 2)
                assert abs(t["gainLoss"] - expected_gl) < 0.02, f"{t['ticker']}: gainLoss={t['gainLoss']} != value-cb={expected_gl}"

    def test_liabilities_are_credit_cards(self, client: TestClient) -> None:
        data = client.get("/allocation", params={"date": "2026-04-07"}).json()
        liab_tickers = [t for t in data["tickers"] if t["category"] == "Liability"]
        assert len(liab_tickers) >= 1
        assert all(t["value"] < 0 for t in liab_tickers)
        liab_sum = sum(t["value"] for t in liab_tickers)
        assert abs(liab_sum - data["liabilities"]) < 0.01

    def test_net_worth_equals_total_plus_liabilities(self, client: TestClient) -> None:
        data = client.get("/allocation", params={"date": "2026-04-07"}).json()
        assert abs(data["netWorth"] - (data["total"] + data["liabilities"])) < 0.01

    def test_tickers_sorted_by_value_desc(self, client: TestClient) -> None:
        data = client.get("/allocation", params={"date": "2026-04-07"}).json()
        values = [t["value"] for t in data["tickers"]]
        assert values == sorted(values, reverse=True)

    def test_ticker_values_sum_to_total(self, client: TestClient) -> None:
        data = client.get("/allocation", params={"date": "2026-04-07"}).json()
        positive_sum = sum(t["value"] for t in data["tickers"] if t["value"] > 0)
        assert abs(positive_sum - data["total"]) < 1.0

    def test_early_date(self, client: TestClient) -> None:
        """Test an early date with less data."""
        resp = client.get("/allocation", params={"date": "2023-04-03"})
        data = resp.json()
        assert data["total"] > 0
        assert data["total"] < 100000  # Early portfolio was small


# ── /activity ──────────────────────────────────────────────────────────────


class TestActivityE2E:
    def test_full_year(self, client: TestClient) -> None:
        resp = client.get("/activity", params={"start": "2025-01-01", "end": "2025-12-31"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["buysBySymbol"]) > 10
        assert len(data["dividendsBySymbol"]) > 5

    def test_buys_have_known_symbols(self, client: TestClient) -> None:
        data = client.get("/activity", params={"start": "2025-01-01", "end": "2025-12-31"}).json()
        symbols = {b["symbol"] for b in data["buysBySymbol"]}
        assert "VOO" in symbols
        assert "QQQM" in symbols

    def test_buys_sorted_by_total(self, client: TestClient) -> None:
        data = client.get("/activity", params={"start": "2025-01-01", "end": "2025-12-31"}).json()
        totals = [b["total"] for b in data["buysBySymbol"]]
        assert totals == sorted(totals, reverse=True)

    def test_narrow_range(self, client: TestClient) -> None:
        """Single month should have fewer transactions."""
        data = client.get("/activity", params={"start": "2025-06-01", "end": "2025-06-30"}).json()
        full_year = client.get("/activity", params={"start": "2025-01-01", "end": "2025-12-31"}).json()
        assert len(data["buysBySymbol"]) <= len(full_year["buysBySymbol"])

    def test_before_account_open(self, client: TestClient) -> None:
        """Before 2023 — no Fidelity account."""
        data = client.get("/activity", params={"start": "2022-01-01", "end": "2022-12-31"}).json()
        assert data["buysBySymbol"] == []
        assert data["dividendsBySymbol"] == []
        assert data["sellsBySymbol"] == []

    def test_sells_exist(self, client: TestClient) -> None:
        data = client.get("/activity", params={"start": "2023-01-01", "end": "2026-04-07"}).json()
        assert len(data["sellsBySymbol"]) > 0
