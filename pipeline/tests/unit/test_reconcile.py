"""Tests for reconciliation logic."""

import pytest

from etl.reconcile import (
    CrossReconciliationData,
    ReconciliationData,
    cross_reconcile,
    portfolio_reconcile,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _qj(date: str, amount: float, note: str = "") -> dict:
    """Create a Qianji transfer dict."""
    return {"date": date, "amount": amount, "note": note}


def _fd(date: str, amount: float, desc: str = "") -> dict:
    """Create a Fidelity deposit dict."""
    return {"date": date, "amount": amount, "description": desc}


# ══════════════════════════════════════════════════════════════════════════════
# cross_reconcile
# ══════════════════════════════════════════════════════════════════════════════


class TestCrossReconcileMatching:
    """Matching behaviour for cross_reconcile."""

    def test_exact_date_and_amount_match(self) -> None:
        qj = [_qj("2026-03-01", 1500.0, "transfer")]
        fd = [_fd("2026-03-01", 1500.0, "EFT")]
        result = cross_reconcile(qj, fd)

        assert len(result.matched) == 1
        assert result.matched[0].amount == pytest.approx(1500.0)
        assert result.matched[0].date_qianji == "2026-03-01"
        assert result.matched[0].date_fidelity == "2026-03-01"

    def test_same_amount_one_day_apart_matches(self) -> None:
        qj = [_qj("2026-03-01", 2000.0)]
        fd = [_fd("2026-03-02", 2000.0)]
        result = cross_reconcile(qj, fd, tolerance_days=1)

        assert len(result.matched) == 1
        assert result.unmatched_qianji == []
        assert result.unmatched_fidelity == []

    def test_same_amount_three_days_apart_unmatched(self) -> None:
        qj = [_qj("2026-03-01", 2000.0)]
        fd = [_fd("2026-03-04", 2000.0)]
        result = cross_reconcile(qj, fd, tolerance_days=1)

        assert result.matched == []
        assert len(result.unmatched_qianji) == 1
        assert len(result.unmatched_fidelity) == 1

    def test_unmatched_qianji_appears_in_list(self) -> None:
        qj = [_qj("2026-03-01", 1500.0), _qj("2026-03-05", 999.0)]
        fd = [_fd("2026-03-01", 1500.0)]
        result = cross_reconcile(qj, fd)

        assert len(result.matched) == 1
        assert len(result.unmatched_qianji) == 1
        assert result.unmatched_qianji[0]["amount"] == pytest.approx(999.0)

    def test_unmatched_fidelity_appears_in_list(self) -> None:
        qj = [_qj("2026-03-01", 1500.0)]
        fd = [_fd("2026-03-01", 1500.0), _fd("2026-03-10", 777.0)]
        result = cross_reconcile(qj, fd)

        assert len(result.matched) == 1
        assert len(result.unmatched_fidelity) == 1
        assert result.unmatched_fidelity[0]["amount"] == pytest.approx(777.0)

    def test_two_identical_amounts_same_day_both_matched(self) -> None:
        qj = [_qj("2026-03-01", 1000.0, "a"), _qj("2026-03-01", 1000.0, "b")]
        fd = [_fd("2026-03-01", 1000.0, "x"), _fd("2026-03-01", 1000.0, "y")]
        result = cross_reconcile(qj, fd)

        assert len(result.matched) == 2
        assert result.unmatched_qianji == []
        assert result.unmatched_fidelity == []


class TestCrossReconcileTotals:
    """Totals and summary fields."""

    def test_totals_are_correct(self) -> None:
        qj = [_qj("2026-03-01", 1500.0), _qj("2026-03-15", 2000.0)]
        fd = [_fd("2026-03-01", 1500.0), _fd("2026-03-20", 500.0)]
        result = cross_reconcile(qj, fd)

        assert result.qianji_total == pytest.approx(3500.0)
        assert result.fidelity_total == pytest.approx(2000.0)

    def test_unmatched_amount(self) -> None:
        qj = [_qj("2026-03-01", 1500.0), _qj("2026-03-15", 2000.0)]
        fd = [_fd("2026-03-01", 1500.0), _fd("2026-03-20", 500.0)]
        result = cross_reconcile(qj, fd)

        # unmatched: qianji 2000 + fidelity 500
        assert result.unmatched_amount == pytest.approx(2500.0)


class TestCrossReconcileEdgeCases:
    """Edge cases."""

    def test_empty_inputs(self) -> None:
        result = cross_reconcile([], [])

        assert isinstance(result, CrossReconciliationData)
        assert result.matched == []
        assert result.unmatched_qianji == []
        assert result.unmatched_fidelity == []
        assert result.qianji_total == pytest.approx(0.0)
        assert result.fidelity_total == pytest.approx(0.0)
        assert result.unmatched_amount == pytest.approx(0.0)

    def test_empty_qianji(self) -> None:
        fd = [_fd("2026-03-01", 500.0)]
        result = cross_reconcile([], fd)

        assert result.matched == []
        assert len(result.unmatched_fidelity) == 1

    def test_empty_fidelity(self) -> None:
        qj = [_qj("2026-03-01", 500.0)]
        result = cross_reconcile(qj, [])

        assert result.matched == []
        assert len(result.unmatched_qianji) == 1


# ══════════════════════════════════════════════════════════════════════════════
# portfolio_reconcile
# ══════════════════════════════════════════════════════════════════════════════


# -- Fixtures ------------------------------------------------------------------

SAMPLE_CONFIG: dict = {
    "assets": {
        "VOO": {"category": "US Equity", "subtype": "broad", "source": "fidelity"},
        "NVDA": {"category": "US Equity", "subtype": "growth", "source": "fidelity"},
        "401k sp500": {"category": "US Equity", "subtype": "broad", "source": "linked"},
        "I Bonds": {"category": "Safe Net", "source": "manual"},
    },
}


class TestPortfolioReconcileBasic:
    """Basic value-change tests."""

    def test_total_change_equals_end_minus_start(self) -> None:
        prev = {"VOO": 10000.0, "401k sp500": 5000.0, "I Bonds": 3000.0}
        curr = {"VOO": 11000.0, "401k sp500": 5500.0, "I Bonds": 3100.0}
        txns: list[dict] = []

        result = portfolio_reconcile(curr, prev, txns, SAMPLE_CONFIG)

        assert result.total_start == pytest.approx(18000.0)
        assert result.total_end == pytest.approx(19600.0)
        assert result.total_change == pytest.approx(1600.0)

    def test_has_dates(self) -> None:
        prev = {"VOO": 10000.0}
        curr = {"VOO": 10500.0}

        result = portfolio_reconcile(curr, prev, [], SAMPLE_CONFIG)

        assert isinstance(result, ReconciliationData)
        assert isinstance(result.prev_date, str)
        assert isinstance(result.curr_date, str)


class TestPortfolioReconcileFidelityTier:
    """Fidelity tier details: deposits, trades, dividends, market_movement."""

    def test_market_movement_is_implied(self) -> None:
        prev = {"VOO": 10000.0, "NVDA": 5000.0}
        curr = {"VOO": 11500.0, "NVDA": 5800.0}
        txns = [
            {"action_type": "deposit", "amount": 1500.0, "symbol": "", "date": "2026-03-15"},
            {"action_type": "buy", "amount": -1000.0, "symbol": "VOO", "date": "2026-03-15"},
            {"action_type": "dividend", "amount": 50.0, "symbol": "VOO", "date": "2026-03-20"},
        ]

        result = portfolio_reconcile(curr, prev, txns, SAMPLE_CONFIG)
        fid = result.fidelity

        assert fid.start_value == pytest.approx(15000.0)
        assert fid.end_value == pytest.approx(17300.0)
        assert fid.net_change == pytest.approx(2300.0)

        # market_movement = net_change - deposits - trades_net - dividends_net
        # trades_net: buy -1000 is money spent on shares, not new money in
        # deposits: 1500, dividends: 50
        # market_movement = 2300 - 1500 - 0 - 50 = 750
        assert fid.details["deposits"] == pytest.approx(1500.0)
        assert fid.details["dividends_net"] == pytest.approx(50.0)
        assert fid.details["trades_net"] == pytest.approx(0.0)
        assert fid.details["market_movement"] == pytest.approx(750.0)

    def test_fidelity_tier_with_no_transactions(self) -> None:
        prev = {"VOO": 10000.0}
        curr = {"VOO": 10200.0}

        result = portfolio_reconcile(curr, prev, [], SAMPLE_CONFIG)
        fid = result.fidelity

        assert fid.details["deposits"] == pytest.approx(0.0)
        assert fid.details["trades_net"] == pytest.approx(0.0)
        assert fid.details["dividends_net"] == pytest.approx(0.0)
        assert fid.details["market_movement"] == pytest.approx(200.0)

    def test_sell_counted_in_trades_net(self) -> None:
        prev = {"VOO": 10000.0}
        curr = {"VOO": 9000.0}
        txns = [
            {"action_type": "sell", "amount": 500.0, "symbol": "VOO", "date": "2026-03-15"},
        ]

        result = portfolio_reconcile(curr, prev, txns, SAMPLE_CONFIG)

        # trades_net includes sell proceeds that leave the portfolio value
        # But sell doesn't remove money from the account -- it just converts shares to cash
        # So trades_net for sells = 0 (cash stays in the account)
        # Actually, trades_net should be 0 for buys AND sells since they don't change total account value
        assert result.fidelity.details["trades_net"] == pytest.approx(0.0)


class TestPortfolioReconcileLinkedTier:
    """Linked tier: per-ticker net change."""

    def test_linked_per_ticker_change(self) -> None:
        prev = {"401k sp500": 5000.0}
        curr = {"401k sp500": 5400.0}

        result = portfolio_reconcile(curr, prev, [], SAMPLE_CONFIG)
        linked = result.linked

        assert linked.start_value == pytest.approx(5000.0)
        assert linked.end_value == pytest.approx(5400.0)
        assert linked.net_change == pytest.approx(400.0)
        assert linked.details["401k sp500"] == pytest.approx(400.0)


class TestPortfolioReconcileManualTier:
    """Manual tier: per-asset net change."""

    def test_manual_per_asset_change(self) -> None:
        prev = {"I Bonds": 3000.0}
        curr = {"I Bonds": 3100.0}

        result = portfolio_reconcile(curr, prev, [], SAMPLE_CONFIG)
        manual = result.manual

        assert manual.start_value == pytest.approx(3000.0)
        assert manual.end_value == pytest.approx(3100.0)
        assert manual.net_change == pytest.approx(100.0)
        assert manual.details["I Bonds"] == pytest.approx(100.0)


class TestPortfolioReconcileTierSums:
    """Totals across all tiers should sum correctly."""

    def test_tier_sums_match_totals(self) -> None:
        prev = {"VOO": 10000.0, "NVDA": 5000.0, "401k sp500": 5000.0, "I Bonds": 3000.0}
        curr = {"VOO": 11000.0, "NVDA": 5500.0, "401k sp500": 5400.0, "I Bonds": 3100.0}

        result = portfolio_reconcile(curr, prev, [], SAMPLE_CONFIG)

        tier_start = result.fidelity.start_value + result.linked.start_value + result.manual.start_value
        tier_end = result.fidelity.end_value + result.linked.end_value + result.manual.end_value
        tier_change = result.fidelity.net_change + result.linked.net_change + result.manual.net_change

        assert tier_start == pytest.approx(result.total_start)
        assert tier_end == pytest.approx(result.total_end)
        assert tier_change == pytest.approx(result.total_change)

    def test_new_ticker_in_current_only(self) -> None:
        """A ticker present in current but not previous starts from 0."""
        prev = {"VOO": 10000.0}
        curr = {"VOO": 10500.0, "NVDA": 2000.0}

        result = portfolio_reconcile(curr, prev, [], SAMPLE_CONFIG)

        assert result.total_start == pytest.approx(10000.0)
        assert result.total_end == pytest.approx(12500.0)

    def test_ticker_disappeared_from_current(self) -> None:
        """A ticker in previous but not current ends at 0."""
        prev = {"VOO": 10000.0, "NVDA": 5000.0}
        curr = {"VOO": 10500.0}

        result = portfolio_reconcile(curr, prev, [], SAMPLE_CONFIG)

        assert result.total_start == pytest.approx(15000.0)
        assert result.total_end == pytest.approx(10500.0)
