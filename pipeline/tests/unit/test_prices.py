"""Tests for prices.py: price loading, caching, and holding periods."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

pytest.importorskip("yfinance", reason="yfinance required for prices module")

from etl.db import get_connection  # noqa: E402
from etl.prices import (  # noqa: E402
    SplitValidationError,
    _holding_periods_from_action_kind_rows,
    _reverse_split_factor,
    _validate_splits_against_transactions,
    fetch_and_store_cny_rates,
    fetch_and_store_prices,
    load_cny_rates,
    load_prices,
)
from etl.sources import ActionKind  # noqa: E402

# ── Helpers ─────────────────────────────────────────────────────────────────


def _seed_prices(db_path: Path, records: list[tuple[str, str, float]]) -> None:
    """Insert (symbol, date, close) records into daily_close."""
    conn = get_connection(db_path)
    for sym, dt, close in records:
        conn.execute(
            "INSERT OR REPLACE INTO daily_close (symbol, date, close) VALUES (?, ?, ?)",
            (sym, dt, close),
        )
    conn.commit()
    conn.close()


# ── load_prices ─────────────────────────────────────────────────────────────


class TestLoadPrices:
    def test_returns_dataframe_with_correct_shape(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_prices(db_path, [
            ("VTI", "2025-01-02", 200.0),
            ("VTI", "2025-01-03", 201.0),
            ("VXUS", "2025-01-02", 60.0),
            ("VXUS", "2025-01-03", 60.5),
        ])
        df = load_prices(db_path)
        assert df.shape == (2, 2)
        assert "VTI" in df.columns
        assert "VXUS" in df.columns

    def test_forward_fills_gaps(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_prices(db_path, [
            ("VTI", "2025-01-02", 200.0),
            ("VTI", "2025-01-03", 201.0),
            ("VXUS", "2025-01-02", 60.0),
        ])
        df = load_prices(db_path)
        assert df.loc[date(2025, 1, 3), "VXUS"] == 60.0

    def test_excludes_cny_rate(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_prices(db_path, [
            ("VTI", "2025-01-02", 200.0),
            ("CNY=X", "2025-01-02", 7.25),
        ])
        df = load_prices(db_path)
        assert "CNY=X" not in df.columns
        assert "VTI" in df.columns

    def test_empty_db_returns_empty_dataframe(self, empty_db: Path) -> None:
        db_path = empty_db
        df = load_prices(db_path)
        assert df.empty

    def test_sorted_by_date(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_prices(db_path, [
            ("VTI", "2025-01-05", 202.0),
            ("VTI", "2025-01-02", 200.0),
            ("VTI", "2025-01-03", 201.0),
        ])
        df = load_prices(db_path)
        dates = list(df.index)
        assert dates == sorted(dates)


# ── load_cny_rates ──────────────────────────────────────────────────────────


class TestLoadCnyRates:
    def test_loads_rates_as_dict(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_prices(db_path, [
            ("CNY=X", "2025-01-02", 7.25),
            ("CNY=X", "2025-01-03", 7.26),
        ])
        rates = load_cny_rates(db_path)
        assert len(rates) == 2
        assert rates[date(2025, 1, 2)] == 7.25
        assert rates[date(2025, 1, 3)] == 7.26

    def test_empty_db_returns_empty_dict(self, empty_db: Path) -> None:
        db_path = empty_db
        rates = load_cny_rates(db_path)
        assert rates == {}

    def test_only_returns_cny_rates(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_prices(db_path, [
            ("CNY=X", "2025-01-02", 7.25),
            ("VTI", "2025-01-02", 200.0),
        ])
        rates = load_cny_rates(db_path)
        assert len(rates) == 1


# ── _holding_periods_from_action_kind_rows ─────────────────────────────────


class TestHoldingPeriodsCore:
    """Test the shared holding-period logic with pre-normalized tuples
    ``(run_date_iso, symbol, action_kind, qty)``."""

    def test_buy_and_hold(self) -> None:
        rows = [
            ("2025-01-02", "VOO", ActionKind.BUY.value, 10.0),
        ]
        result = _holding_periods_from_action_kind_rows(rows)
        assert result["VOO"] == (date(2025, 1, 2), None)

    def test_buy_then_sell_to_zero(self) -> None:
        rows = [
            ("2025-01-02", "VOO", ActionKind.BUY.value, 10.0),
            ("2025-03-15", "VOO", ActionKind.SELL.value, -10.0),
        ]
        result = _holding_periods_from_action_kind_rows(rows)
        assert result["VOO"] == (date(2025, 1, 2), date(2025, 3, 15))

    def test_money_market_excluded(self) -> None:
        rows = [
            ("2025-01-02", "SPAXX", ActionKind.REINVESTMENT.value, 100.0),
        ]
        result = _holding_periods_from_action_kind_rows(rows)
        assert "SPAXX" not in result

    def test_cusip_excluded(self) -> None:
        rows = [
            ("2025-01-02", "912796CR8", ActionKind.BUY.value, 5.0),
        ]
        result = _holding_periods_from_action_kind_rows(rows)
        assert "912796CR8" not in result

    def test_partial_sell_still_held(self) -> None:
        rows = [
            ("2025-01-02", "VOO", ActionKind.BUY.value, 10.0),
            ("2025-03-15", "VOO", ActionKind.SELL.value, -4.0),
        ]
        result = _holding_periods_from_action_kind_rows(rows)
        # Still held — end should be None
        assert result["VOO"] == (date(2025, 1, 2), None)

    def test_empty_rows(self) -> None:
        result = _holding_periods_from_action_kind_rows([])
        assert result == {}

    def test_redemption_acts_as_sell(self) -> None:
        """REDEMPTION (e.g. T-Bill payout) reduces qty without cost-basis
        impact and counts as a position-affecting kind for holding periods."""
        rows = [
            ("2025-01-02", "SGOV", ActionKind.BUY.value, 1000.0),
            ("2025-06-15", "SGOV", ActionKind.REDEMPTION.value, -1000.0),
        ]
        result = _holding_periods_from_action_kind_rows(rows)
        assert result["SGOV"] == (date(2025, 1, 2), date(2025, 6, 15))


# ── Invariant: historical daily_close rows are immutable ───────────────────


def _cny_df(rows: list[tuple[str, float]]) -> pd.DataFrame:
    """Build a yfinance-style single-symbol DataFrame.

    Shape matches what ``yf.download("CNY=X", ...)`` returns: a DataFrame
    indexed by DatetimeIndex with a flat "Close" column.
    """
    return pd.DataFrame(
        {"Close": [c for _, c in rows]},
        index=pd.to_datetime([d for d, _ in rows]),
    )


class TestHistoricalImmutabilityCnyRates:
    """`fetch_and_store_cny_rates` must never overwrite historical values.

    Yahoo occasionally returns partial or revised data for past dates. Once a
    rate is stored for a date older than the refresh window, it should be
    treated as the authoritative historical value. Only recent dates (within
    the refresh window) may be updated — Yahoo sometimes publishes late
    corrections for the past few days.
    """

    def test_historical_row_preserved_when_yahoo_returns_different_value(
        self, empty_db: Path,
    ) -> None:
        db_path = empty_db
        _seed_prices(db_path, [("CNY=X", "2023-03-13", 6.9052)])

        with patch("etl.prices.fetch.yf.download") as mock_dl:
            mock_dl.return_value = _cny_df([("2023-03-13", 99.0)])
            fetch_and_store_cny_rates(db_path, date(2023, 3, 13), date(2026, 4, 12))

        # Historical row unchanged
        conn = get_connection(db_path)
        r = conn.execute(
            "SELECT close FROM daily_close WHERE symbol='CNY=X' AND date='2023-03-13'",
        ).fetchone()
        conn.close()
        assert r[0] == pytest.approx(6.9052)

    def test_historical_gap_filled_without_touching_existing(
        self, empty_db: Path,
    ) -> None:
        db_path = empty_db
        _seed_prices(db_path, [
            ("CNY=X", "2023-07-05", 7.2135),  # existing
        ])

        with patch("etl.prices.fetch.yf.download") as mock_dl:
            # Yahoo returns BOTH the existing date (with different value) and a
            # historical gap date.
            mock_dl.return_value = _cny_df([
                ("2023-03-13", 6.9052),  # new gap-fill
                ("2023-07-05", 99.0),    # conflict; must be ignored
            ])
            fetch_and_store_cny_rates(db_path, date(2023, 3, 13), date(2026, 4, 12))

        conn = get_connection(db_path)
        existing = conn.execute(
            "SELECT close FROM daily_close WHERE symbol='CNY=X' AND date='2023-07-05'",
        ).fetchone()
        gap = conn.execute(
            "SELECT close FROM daily_close WHERE symbol='CNY=X' AND date='2023-03-13'",
        ).fetchone()
        conn.close()
        assert existing[0] == pytest.approx(7.2135)  # preserved
        assert gap[0] == pytest.approx(6.9052)       # filled

    def test_recent_row_NOT_refreshed(
        self, empty_db: Path,
    ) -> None:
        """CNY=X is INSERT OR IGNORE for every date (not just historical).

        Equity prices refresh within a 7-day window so intraday ticks land,
        but FX rates are intentionally pinned the moment they're first
        captured — intraday USD/CNY drift otherwise makes two back-to-back
        rebuilds produce different computed_daily hashes. See commit
        ``5a0468d`` (stabilize CNY=X) for the full reasoning.
        """
        db_path = empty_db
        _seed_prices(db_path, [("CNY=X", "2026-04-10", 7.20)])  # already captured

        with patch("etl.prices.fetch.yf.download") as mock_dl:
            mock_dl.return_value = _cny_df([("2026-04-10", 7.25)])  # Yahoo correction
            fetch_and_store_cny_rates(db_path, date(2023, 3, 13), date(2026, 4, 12))

        conn = get_connection(db_path)
        r = conn.execute(
            "SELECT close FROM daily_close WHERE symbol='CNY=X' AND date='2026-04-10'",
        ).fetchone()
        conn.close()
        assert r[0] == pytest.approx(7.20)  # first-capture value pinned


class TestHistoricalImmutabilityPrices:
    """`fetch_and_store_prices` enforces the same invariant for per-symbol prices."""

    def test_historical_price_preserved_when_yahoo_returns_different_value(
        self, empty_db: Path,
    ) -> None:
        db_path = empty_db
        _seed_prices(db_path, [("VOO", "2024-01-15", 440.50)])

        # Open-ended holding period — the recent-window refresh always queues
        # a fetch, so yfinance.download will be called.
        with patch("etl.prices.fetch.yf.download") as mock_dl, \
             patch("etl.prices.fetch._build_split_factors", return_value={}):
            mock_dl.return_value = pd.DataFrame(
                {("Close", "VOO"): [999.0]},
                index=pd.to_datetime(["2024-01-15"]),
            )
            fetch_and_store_prices(
                db_path,
                {"VOO": (date(2024, 1, 15), None)},  # still held → need_end = end
                date(2026, 4, 12),
            )
            # Confirm fetch actually ran (otherwise the test is a no-op).
            assert mock_dl.called

        conn = get_connection(db_path)
        r = conn.execute(
            "SELECT close FROM daily_close WHERE symbol='VOO' AND date='2024-01-15'",
        ).fetchone()
        conn.close()
        assert r[0] == pytest.approx(440.50)  # historical value preserved


class TestFetchGateRefreshesRecentWindow:
    """Regression: the fetch gate must always refresh the recent window.

    Earlier logic skipped the fetch when ``cached_hi`` was within 4 days of
    ``need_end``, which silently left new trading days stale (observed:
    cached_hi=04-10, need_end=04-14 → skip, missing 04-13 and 04-14 closes).
    """

    def test_fetch_triggered_when_cache_is_one_day_stale(self, empty_db: Path) -> None:
        db_path = empty_db
        # Cache ends 2026-04-11; need_end = 2026-04-12. Old logic would skip
        # (04-11 < 04-08 is False). New logic must still fetch recent window.
        _seed_prices(db_path, [
            ("VOO", "2024-01-15", 440.50),
            ("VOO", "2026-04-11", 500.00),
        ])

        with patch("etl.prices.fetch.yf.download") as mock_dl, \
             patch("etl.prices.fetch._build_split_factors", return_value={}):
            mock_dl.return_value = pd.DataFrame(
                {("Close", "VOO"): [505.0]},
                index=pd.to_datetime(["2026-04-12"]),
            )
            fetch_and_store_prices(
                db_path,
                {"VOO": (date(2024, 1, 15), None)},
                date(2026, 4, 12),
            )
            assert mock_dl.called

    def test_fetch_uses_refresh_window_not_full_history(self, empty_db: Path) -> None:
        """When history is covered, fetch only the recent window (not from hp_start)."""
        from etl.refresh import refresh_window_start

        db_path = empty_db
        _seed_prices(db_path, [
            ("VOO", "2024-01-15", 440.50),
            ("VOO", "2026-04-11", 500.00),
        ])
        end = date(2026, 4, 12)

        with patch("etl.prices.fetch.yf.download") as mock_dl, \
             patch("etl.prices.fetch._build_split_factors", return_value={}):
            mock_dl.return_value = pd.DataFrame(
                {("Close", "VOO"): [505.0]},
                index=pd.to_datetime(["2026-04-12"]),
            )
            fetch_and_store_prices(db_path, {"VOO": (date(2024, 1, 15), None)}, end)
            assert mock_dl.called
            start_d = date.fromisoformat(mock_dl.call_args.kwargs["start"])
            # Should be exactly the refresh-window start (not hp_start 2024-01-15)
            assert start_d == refresh_window_start(end)

    def test_fetch_triggered_when_cache_missing_entirely(self, empty_db: Path) -> None:
        """New symbol with no cache → fetch full range from hp_start."""
        db_path = empty_db

        with patch("etl.prices.fetch.yf.download") as mock_dl, \
             patch("etl.prices.fetch._build_split_factors", return_value={}):
            mock_dl.return_value = pd.DataFrame(
                {("Close", "NEW"): [100.0]},
                index=pd.to_datetime(["2026-04-12"]),
            )
            fetch_and_store_prices(
                db_path, {"NEW": (date(2026, 4, 1), None)}, date(2026, 4, 12),
            )
            assert mock_dl.called
            assert date.fromisoformat(mock_dl.call_args.kwargs["start"]) == date(2026, 4, 1)


# ── Split cross-validation ─────────────────────────────────────────────────


def _seed_fidelity_txn(
    db_path: Path,
    run_date: str,
    symbol: str,
    action_kind: str,
    quantity: float,
) -> None:
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO fidelity_transactions"
        " (run_date, account_number, action, action_kind, symbol, quantity)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (run_date, "Z29", f"TEST {action_kind} ({symbol})", action_kind, symbol, quantity),
    )
    conn.commit()
    conn.close()


class TestReverseSplitFactor:
    def test_no_splits(self) -> None:
        assert _reverse_split_factor(date(2024, 1, 1), []) == 1.0

    def test_forward_split_scales_pre_split_dates(self) -> None:
        """A 3:1 forward split → pre-split Close × 3 recovers real market price."""
        factors = [(date(2024, 10, 11), 3.0)]
        assert _reverse_split_factor(date(2024, 10, 10), factors) == 3.0
        assert _reverse_split_factor(date(2024, 10, 11), factors) == 1.0
        assert _reverse_split_factor(date(2024, 10, 12), factors) == 1.0

    def test_reverse_split_scales_pre_split_dates(self) -> None:
        """A 1:10 reverse split (ratio=0.1) → pre-split Close × 0.1 recovers real price.

        Yahoo pre-reverse-split Close is adjusted UP (to match post-split NAV);
        multiplying by 0.1 brings it back down to the actual market price.
        """
        factors = [(date(2024, 6, 1), 0.1)]
        assert _reverse_split_factor(date(2024, 5, 31), factors) == pytest.approx(0.1)
        assert _reverse_split_factor(date(2024, 6, 1), factors) == 1.0

    def test_multiple_splits_compound(self) -> None:
        factors = [(date(2022, 1, 1), 2.0), (date(2024, 1, 1), 3.0)]
        # Pre-both: factor = 2 × 3 = 6
        assert _reverse_split_factor(date(2021, 12, 31), factors) == 6.0
        # Between: factor = 3 only
        assert _reverse_split_factor(date(2023, 6, 1), factors) == 3.0
        # Post-both
        assert _reverse_split_factor(date(2024, 1, 2), factors) == 1.0


class TestSplitCrossValidation:
    """``_validate_splits_against_transactions`` is the backstop that catches
    Yahoo/Fidelity split drift before wrong prices get persisted."""

    def test_matching_split_passes(self, empty_db: Path) -> None:
        """SCHD 3:1 with correct DISTRIBUTION row → validation silent."""
        db_path = empty_db
        _seed_fidelity_txn(db_path, "2024-07-08", "SCHD", "buy", 27.022)
        _seed_fidelity_txn(db_path, "2024-10-11", "SCHD", "distribution", 54.044)
        conn = get_connection(db_path)
        try:
            _validate_splits_against_transactions(
                conn,
                {"SCHD": (date(2024, 7, 8), None)},
                {"SCHD": [(date(2024, 10, 11), 3.0)]},
                today=date(2024, 12, 1),
            )
        finally:
            conn.close()

    def test_missing_distribution_row_raises(self, empty_db: Path) -> None:
        """Yahoo knows about a split but Fidelity CSV has no DISTRIBUTION →
        share count is stale, must fail loud."""
        db_path = empty_db
        _seed_fidelity_txn(db_path, "2024-07-08", "SCHD", "buy", 27.022)
        # No DISTRIBUTION row seeded.
        conn = get_connection(db_path)
        try:
            with pytest.raises(SplitValidationError, match="SCHD"):
                _validate_splits_against_transactions(
                    conn,
                    {"SCHD": (date(2024, 7, 8), None)},
                    {"SCHD": [(date(2024, 10, 11), 3.0)]},
                    today=date(2024, 12, 1),
                )
        finally:
            conn.close()

    def test_wrong_distribution_qty_raises(self, empty_db: Path) -> None:
        """DISTRIBUTION qty doesn't match Yahoo's ratio → data drift, fail."""
        db_path = empty_db
        _seed_fidelity_txn(db_path, "2024-07-08", "SCHD", "buy", 27.022)
        # Expected +54.044 for 3:1; seed only +20 (wrong).
        _seed_fidelity_txn(db_path, "2024-10-11", "SCHD", "distribution", 20.0)
        conn = get_connection(db_path)
        try:
            with pytest.raises(SplitValidationError, match="SCHD.*expected.*54"):
                _validate_splits_against_transactions(
                    conn,
                    {"SCHD": (date(2024, 7, 8), None)},
                    {"SCHD": [(date(2024, 10, 11), 3.0)]},
                    today=date(2024, 12, 1),
                )
        finally:
            conn.close()

    def test_distribution_without_yahoo_split_raises(self, empty_db: Path) -> None:
        """Fidelity has a DISTRIBUTION (qty>0) but Yahoo reports no split on
        that date → Yahoo's split-adjusted pre-split prices would be stored
        un-reversed. Fail loud — this is the silent-yfinance-failure case."""
        db_path = empty_db
        _seed_fidelity_txn(db_path, "2024-07-08", "SCHD", "buy", 27.022)
        _seed_fidelity_txn(db_path, "2024-10-11", "SCHD", "distribution", 54.044)
        conn = get_connection(db_path)
        try:
            with pytest.raises(SplitValidationError, match="SCHD 2024-10-11"):
                _validate_splits_against_transactions(
                    conn,
                    {"SCHD": (date(2024, 7, 8), None)},
                    {},  # empty → simulates _build_split_factors silent failure
                    today=date(2024, 12, 1),
                )
        finally:
            conn.close()

    def test_split_outside_holding_period_ignored(self, empty_db: Path) -> None:
        """Bought AFTER the split date → no DISTRIBUTION expected, validation OK."""
        db_path = empty_db
        # User bought NVDA AFTER the 2024-06-10 split.
        _seed_fidelity_txn(db_path, "2024-11-27", "NVDA", "buy", 3.0)
        conn = get_connection(db_path)
        try:
            _validate_splits_against_transactions(
                conn,
                {"NVDA": (date(2024, 11, 27), None)},
                {"NVDA": [(date(2024, 6, 10), 10.0)]},
                today=date(2024, 12, 1),
            )
        finally:
            conn.close()

    def test_reverse_split_pair_passes(self, empty_db: Path) -> None:
        """Reverse 1:10 split (ratio=0.1) on 100 pre-split shares → Fidelity
        records a REDEMPTION (-100) + DISTRIBUTION (+10) pair whose net is
        -90, matching ``pre_qty × (ratio - 1) = 100 × -0.9 = -90``. The
        validator must accept this without false-positive; previously only
        the DISTRIBUTION leg was queried and the REDEMPTION row was ignored,
        so the check fired ``expected=-90, got=+10``."""
        db_path = empty_db
        # Pre-split holding: 100 shares bought before the split.
        _seed_fidelity_txn(db_path, "2023-01-15", "BOGUS", "buy", 100.0)
        # Reverse split day: turn-in 100 old shares + receive 10 new.
        _seed_fidelity_txn(db_path, "2024-06-01", "BOGUS", "redemption", -100.0)
        _seed_fidelity_txn(db_path, "2024-06-01", "BOGUS", "distribution", 10.0)
        conn = get_connection(db_path)
        try:
            _validate_splits_against_transactions(
                conn,
                {"BOGUS": (date(2023, 1, 15), None)},
                {"BOGUS": [(date(2024, 6, 1), 0.1)]},
                today=date(2024, 12, 1),
            )
        finally:
            conn.close()

    def test_reverse_split_with_missing_redemption_raises(self, empty_db: Path) -> None:
        """Reverse split where only the DISTRIBUTION leg (+10) made it into the
        DB — the REDEMPTION (-100) is missing, so net=+10 but expected=-90.
        Must fail loud so the operator knows Fidelity's CSV is incomplete."""
        db_path = empty_db
        _seed_fidelity_txn(db_path, "2023-01-15", "BOGUS", "buy", 100.0)
        # Missing the REDEMPTION leg — only +10 distribution recorded.
        _seed_fidelity_txn(db_path, "2024-06-01", "BOGUS", "distribution", 10.0)
        conn = get_connection(db_path)
        try:
            with pytest.raises(SplitValidationError, match="BOGUS.*expected.*-90"):
                _validate_splits_against_transactions(
                    conn,
                    {"BOGUS": (date(2023, 1, 15), None)},
                    {"BOGUS": [(date(2024, 6, 1), 0.1)]},
                    today=date(2024, 12, 1),
                )
        finally:
            conn.close()

    def test_multi_mismatch_report_includes_all(self, empty_db: Path) -> None:
        """Every mismatch is aggregated into a single error message so the
        operator sees the full damage in one run."""
        db_path = empty_db
        _seed_fidelity_txn(db_path, "2024-07-08", "SCHD", "buy", 27.022)
        _seed_fidelity_txn(db_path, "2024-07-08", "AVGO", "buy", 1.007)
        # Neither DISTRIBUTION seeded → both directions fire.
        conn = get_connection(db_path)
        try:
            with pytest.raises(SplitValidationError) as exc:
                _validate_splits_against_transactions(
                    conn,
                    {
                        "SCHD": (date(2024, 7, 8), None),
                        "AVGO": (date(2024, 7, 8), None),
                    },
                    {
                        "SCHD": [(date(2024, 10, 11), 3.0)],
                        "AVGO": [(date(2024, 7, 15), 10.0)],
                    },
                    today=date(2024, 12, 1),
                )
            msg = str(exc.value)
            assert "SCHD" in msg
            assert "AVGO" in msg
        finally:
            conn.close()

    def test_same_day_split_and_special_stock_distribution_reported(
        self, empty_db: Path,
    ) -> None:
        """A ticker with BOTH a 2:1 split AND a same-day special
        stock-distribution must surface the extra qty as a separate mismatch
        without blaming the split.

        Pre-fix behaviour: direction 2 aggregates by ``(symbol, run_date)``,
        so the two DISTRIBUTION rows collapse into one. Direction 1 would
        fire with a confusing ``got != expected`` on the split line, and the
        extra event would not be distinctly named. After the fix, direction
        1's split check passes (split delta matches), and the residual
        surfaces as "extra DISTRIBUTION qty ... not accounted for by the
        split ratio" — a clear signal to the operator that a co-occurring
        special distribution landed on a split day.
        """
        db_path = empty_db
        # Holding 100 VOO prior to the split date.
        _seed_fidelity_txn(db_path, "2025-06-10", "VOO", "buy", 100.0)
        # 2025-12-15: 2-for-1 split → +100 shares (split delta).
        _seed_fidelity_txn(db_path, "2025-12-15", "VOO", "distribution", 100.0)
        # Same date, a separate special stock distribution → +5 shares.
        _seed_fidelity_txn(db_path, "2025-12-15", "VOO", "distribution", 5.0)
        conn = get_connection(db_path)
        try:
            with pytest.raises(SplitValidationError) as exc:
                _validate_splits_against_transactions(
                    conn,
                    {"VOO": (date(2025, 6, 10), None)},
                    {"VOO": [(date(2025, 12, 15), 2.0)]},
                    today=date(2026, 1, 1),
                )
            msg = str(exc.value)
            # Direction 1 should NOT raise the underlying split-delta line
            # (split is matched): the old "expected DISTRIBUTION+REDEMPTION
            # net=... got=..." text must be absent.
            assert "expected DISTRIBUTION+REDEMPTION net" not in msg
            # Residual path surfaces the 5-share extra on the split date.
            assert "VOO 2025-12-15" in msg
            assert "split delta matched" in msg
            assert "extra DISTRIBUTION qty+=5" in msg
            # And nothing slipped through direction 2's "no matching Yahoo
            # split" path — the date DID have a Yahoo entry.
            assert "no matching Yahoo split" not in msg
        finally:
            conn.close()

    def test_same_day_split_matching_exactly_still_passes(
        self, empty_db: Path,
    ) -> None:
        """Guard the happy path: a single DISTRIBUTION row that exactly
        matches ``pre_qty × (ratio - 1)`` must still pass silently after the
        residual-check refactor (no residual, no raise)."""
        db_path = empty_db
        _seed_fidelity_txn(db_path, "2025-06-10", "VOO", "buy", 100.0)
        _seed_fidelity_txn(db_path, "2025-12-15", "VOO", "distribution", 100.0)
        conn = get_connection(db_path)
        try:
            _validate_splits_against_transactions(
                conn,
                {"VOO": (date(2025, 6, 10), None)},
                {"VOO": [(date(2025, 12, 15), 2.0)]},
                today=date(2026, 1, 1),
            )
        finally:
            conn.close()


# ── Incremental fetch regression ───────────────────────────────────────────


class TestIncrementalFetch:
    def test_cached_lo_after_global_start_does_not_trigger_full_batch(
        self, empty_db: Path,
    ) -> None:
        """Regression: a symbol whose cache starts later than ``global_start``
        (e.g. IPO'd after the user's timeline began) must NOT trigger a
        multi-year batch fetch.

        Before the fix, ``cached_lo > fetch_start`` was treated as a
        "historical gap" and the symbol's ``to_fetch`` start was reset to
        ``fetch_start`` (=``global_start``). Since ``batch_start`` is the
        MIN across all symbols, one IPO'd-late ticker dragged every other
        symbol to a full-history fetch — effectively turning every daily
        sync into a 3-year × 83-symbol download. Yahoo rate-limited and
        returned empty for many symbols, which then broke the sync gate
        because the local DB lost rows vs prod.

        With the fix, any cached symbol just refreshes the recent window.
        """
        from etl.refresh import refresh_window_start

        db_path = empty_db
        # Two symbols: VOO cached from 2023 (old, full-history), FBTC
        # cached only from 2024-01-11 (IPO'd later).
        _seed_prices(db_path, [
            ("VOO", "2023-03-13", 380.0),
            ("VOO", "2026-04-11", 500.0),
            ("FBTC", "2024-01-11", 30.0),
            ("FBTC", "2026-04-11", 85.0),
        ])
        end = date(2026, 4, 12)

        with patch("etl.prices.fetch.yf.download") as mock_dl, \
             patch("etl.prices.fetch._build_split_factors", return_value={}):
            mock_dl.return_value = pd.DataFrame(
                {("Close", "VOO"): [505.0], ("Close", "FBTC"): [86.0]},
                index=pd.to_datetime(["2026-04-12"]),
            )
            fetch_and_store_prices(
                db_path,
                {
                    "VOO": (date(2023, 3, 13), None),
                    "FBTC": (date(2024, 1, 11), None),
                },
                end,
                global_start=date(2023, 3, 13),  # brush-range floor
            )
            assert mock_dl.called
            start_d = date.fromisoformat(mock_dl.call_args.kwargs["start"])
            # Batch must be the refresh window, not the 3-year span.
            assert start_d == refresh_window_start(end), (
                f"expected {refresh_window_start(end)}, got {start_d} — the "
                f"IPO'd-late symbol must not drag batch_start back to global_start"
            )
