"""Tests for FRED API data fetcher."""

from __future__ import annotations

import pytest

pytest.importorskip("fredapi", reason="fredapi required for FRED tests")

from unittest.mock import MagicMock, patch  # noqa: E402

import pandas as pd  # noqa: E402

# ── Helpers ─────────────────────────────────────────────────────────────────

def _daily_series(values: list[float], start: str = "2025-03-24") -> pd.Series:
    """Build a pandas Series with business-day index (5 days)."""
    dates = pd.bdate_range(start=start, periods=len(values))
    return pd.Series(values, index=dates, dtype=float)


def _monthly_series(values: list[float], start: str = "2025-01-01") -> pd.Series:
    """Build a pandas Series with month-start index."""
    dates = pd.date_range(start=start, periods=len(values), freq="MS")
    return pd.Series(values, index=dates, dtype=float)


def _cpi_series(values: list[float], start: str = "2024-01-01") -> pd.Series:
    """Build a CPI-style pandas Series spanning 12+ months for YoY calc.

    Default: 16 monthly points starting 2024-01 so that the first YoY value
    can be computed at 2025-01 (index 12).
    """
    dates = pd.date_range(start=start, periods=len(values), freq="MS")
    return pd.Series(values, index=dates, dtype=float)


def _build_mock_fred() -> MagicMock:
    """Return a mock Fred instance whose get_series returns plausible data."""
    mock_fred = MagicMock()

    # Daily series — 5 business days
    daily_data = {
        "DFF": _daily_series([5.33, 5.33, 5.33, 5.33, 5.33]),
        "DGS10": _daily_series([4.20, 4.22, 4.25, 4.23, 4.21]),
        "DGS2": _daily_series([3.90, 3.92, 3.95, 3.93, 3.91]),
        "VIXCLS": _daily_series([15.0, 16.0, 14.5, 15.5, 15.2]),
        "DCOILWTICO": _daily_series([78.0, 79.0, 77.5, 78.5, 78.2]),
    }

    # Monthly series — 4 points
    monthly_data = {
        "UNRATE": _monthly_series([3.7, 3.8, 3.9, 4.0]),
    }

    # CPI series — 16 points spanning 2024-01 to 2025-04 (>12 months for YoY)
    cpi_all = [300.0 + i * 0.5 for i in range(16)]  # slow upward trend
    cpi_core = [305.0 + i * 0.4 for i in range(16)]
    cpi_data = {
        "CPIAUCSL": _cpi_series(cpi_all),
        "CPILFESL": _cpi_series(cpi_core),
    }

    def _get_series(series_id: str, **_kwargs: object) -> pd.Series:
        all_data = {**daily_data, **monthly_data, **cpi_data}
        if series_id not in all_data:
            raise ValueError(f"Unknown series: {series_id}")
        return all_data[series_id]

    mock_fred.get_series = MagicMock(side_effect=_get_series)
    return mock_fred


# ── Happy path ──────────────────────────────────────────────────────────────


class TestFetchFredDataHappyPath:
    """fetch_fred_data returns properly structured data on success."""

    @patch("etl.market.fred.Fred")
    def test_returns_snapshot_and_series(self, mock_fred_cls: MagicMock) -> None:
        """Result contains both 'snapshot' and 'series' top-level keys."""
        mock_fred_cls.return_value = _build_mock_fred()

        from etl.market.fred import fetch_fred_data

        result = fetch_fred_data("test_key")

        assert result is not None
        assert "snapshot" in result
        assert "series" in result

    @patch("etl.market.fred.Fred")
    def test_snapshot_has_expected_fields(self, mock_fred_cls: MagicMock) -> None:
        """Snapshot contains camelCase keys for all indicator latest values."""
        mock_fred_cls.return_value = _build_mock_fred()

        from etl.market.fred import fetch_fred_data

        result = fetch_fred_data("test_key")
        assert result is not None
        snap = result["snapshot"]

        expected_keys = {
            "fedFundsRate", "treasury10y", "treasury2y", "spread2s10s",
            "vix", "oilWti", "unemployment", "cpiYoy", "coreCpiYoy",
        }
        assert expected_keys.issubset(set(snap.keys())), (
            f"Missing keys: {expected_keys - set(snap.keys())}"
        )

    @patch("etl.market.fred.Fred")
    def test_series_entries_have_date_value_format(self, mock_fred_cls: MagicMock) -> None:
        """Every series entry is a list of {date: 'YYYY-MM', value: number}."""
        mock_fred_cls.return_value = _build_mock_fred()

        from etl.market.fred import fetch_fred_data

        result = fetch_fred_data("test_key")
        assert result is not None

        for key, entries in result["series"].items():
            assert isinstance(entries, list), f"series[{key}] should be a list"
            for entry in entries:
                assert "date" in entry, f"series[{key}] entry missing 'date'"
                assert "value" in entry, f"series[{key}] entry missing 'value'"
                # Date format: YYYY-MM
                assert len(entry["date"]) == 7, f"Bad date format: {entry['date']}"
                assert entry["date"][4] == "-", f"Bad date separator: {entry['date']}"
                assert isinstance(entry["value"], (int, float)), f"Value should be numeric: {entry['value']}"

    @patch("etl.market.fred.Fred")
    def test_cpi_returned_as_yoy_percentage(self, mock_fred_cls: MagicMock) -> None:
        """CPI values are YoY percentage changes, not raw index values."""
        mock_fred_cls.return_value = _build_mock_fred()

        from etl.market.fred import fetch_fred_data

        result = fetch_fred_data("test_key")
        assert result is not None

        # CPI YoY should be small percentages, not 300+ raw index values
        cpi_yoy = result["snapshot"]["cpiYoy"]
        assert -10 < cpi_yoy < 30, f"CPI YoY looks like a raw index, not %%: {cpi_yoy}"

        core_cpi_yoy = result["snapshot"]["coreCpiYoy"]
        assert -10 < core_cpi_yoy < 30, f"Core CPI YoY looks like a raw index, not %%: {core_cpi_yoy}"

        # Verify the series values are also YoY percentages
        cpi_series = result["series"]["cpiYoy"]
        for entry in cpi_series:
            assert -10 < entry["value"] < 30, f"CPI series entry looks like raw index: {entry}"

    @patch("etl.market.fred.Fred")
    def test_2s10s_spread_is_computed(self, mock_fred_cls: MagicMock) -> None:
        """spread2s10s = treasury10y - treasury2y."""
        mock_fred_cls.return_value = _build_mock_fred()

        from etl.market.fred import fetch_fred_data

        result = fetch_fred_data("test_key")
        assert result is not None

        spread = result["snapshot"]["spread2s10s"]
        t10 = result["snapshot"]["treasury10y"]
        t2 = result["snapshot"]["treasury2y"]
        assert spread == pytest.approx(t10 - t2, abs=0.01)

    @patch("etl.market.fred.Fred")
    def test_series_has_expected_keys(self, mock_fred_cls: MagicMock) -> None:
        """Series dict contains keys for each indicator."""
        mock_fred_cls.return_value = _build_mock_fred()

        from etl.market.fred import fetch_fred_data

        result = fetch_fred_data("test_key")
        assert result is not None

        expected_series = {
            "fedFundsRate", "treasury10y", "treasury2y", "spread2s10s",
            "vix", "oilWti", "unemployment", "cpiYoy", "coreCpiYoy",
        }
        assert expected_series.issubset(set(result["series"].keys()))


# ── Edge cases / failures ───────────────────────────────────────────────────


class TestFetchFredDataEdgeCases:
    """Error handling: empty keys, API failures."""

    def test_empty_api_key_returns_none(self) -> None:
        """Empty string API key returns None without calling FRED."""
        from etl.market.fred import fetch_fred_data

        assert fetch_fred_data("") is None

    @patch("etl.market.fred.Fred")
    def test_api_failure_does_not_raise(self, mock_fred_cls: MagicMock) -> None:
        """Full API failure returns None, never raises."""
        mock_fred_cls.side_effect = Exception("auth error")

        from etl.market.fred import fetch_fred_data

        result = fetch_fred_data("bad_key")
        assert result is None

    @patch("etl.market.fred.Fred")
    def test_partial_series_failure_still_returns_data(self, mock_fred_cls: MagicMock) -> None:
        """If one series fails, the rest are still returned."""
        mock_fred = _build_mock_fred()
        original_get = mock_fred.get_series.side_effect

        call_count = 0

        def _fail_first(series_id: str, **kwargs: object) -> pd.Series:
            nonlocal call_count
            call_count += 1
            # Fail the DFF (fed funds rate) series
            if series_id == "DFF":
                raise Exception("FRED API timeout")
            return original_get(series_id, **kwargs)

        mock_fred.get_series = MagicMock(side_effect=_fail_first)
        mock_fred_cls.return_value = mock_fred

        from etl.market.fred import fetch_fred_data

        result = fetch_fred_data("test_key")

        # Should still return data (not None) — just missing the failed series
        assert result is not None
        assert "snapshot" in result
        assert "series" in result

    @patch("etl.market.fred.Fred")
    def test_snapshot_values_are_floats(self, mock_fred_cls: MagicMock) -> None:
        """All snapshot values should be numeric (float)."""
        mock_fred_cls.return_value = _build_mock_fred()

        from etl.market.fred import fetch_fred_data

        result = fetch_fred_data("test_key")
        assert result is not None

        for key, value in result["snapshot"].items():
            assert isinstance(value, (int, float)), f"snapshot[{key}] should be numeric, got {type(value)}"
