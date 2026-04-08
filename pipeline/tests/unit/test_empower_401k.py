"""Tests for empower_401k: QFX parsing, contributions, and daily interpolation."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from generate_asset_snapshot.empower_401k import (
    Contribution,
    FundSnapshot,
    QuarterSnapshot,
    _ffill_proxy,
    daily_401k_values,
    load_all_contributions,
    load_all_qfx,
    parse_qfx,
    parse_qfx_contributions,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

QFX_WITH_BUYMF = """\
OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
  <INVSTMTMSGSRSV1>
    <INVSTMTTRNRS>
      <INVSTMTRS>
        <DTASOF>20250630000000.000</DTASOF>
        <CURDEF>USD</CURDEF>
        <INVTRANLIST>
          <DTSTART>20250401000000.000</DTSTART>
          <DTEND>20250630000000.000</DTEND>
          <BUYMF>
            <INVBUY>
              <INVTRAN>
                <FITID>20250415001</FITID>
                <DTTRADE>20250415</DTTRADE>
              </INVTRAN>
              <SECID>
                <UNIQUEID>856917729</UNIQUEID>
                <UNIQUEIDTYPE>CUSIP</UNIQUEIDTYPE>
              </SECID>
              <UNITS>5.0</UNITS>
              <UNITPRICE>10.0</UNITPRICE>
              <TOTAL>-50.00</TOTAL>
            </INVBUY>
          <BUYMF>
            <INVBUY>
              <INVTRAN>
                <FITID>20250415002</FITID>
                <DTTRADE>20250501</DTTRADE>
              </INVTRAN>
              <SECID>
                <UNIQUEID>41150L691</UNIQUEID>
                <UNIQUEIDTYPE>CUSIP</UNIQUEIDTYPE>
              </SECID>
              <UNITS>3.0</UNITS>
              <UNITPRICE>20.0</UNITPRICE>
              <TOTAL>-60.00</TOTAL>
            </INVBUY>
        </INVTRANLIST>
        <INVPOSLIST>
          <POSMF>
            <INVPOS>
              <SECID>
                <UNIQUEID>856917729</UNIQUEID>
                <UNIQUEIDTYPE>CUSIP</UNIQUEIDTYPE>
              </SECID>
              <UNITS>100.0</UNITS>
              <UNITPRICE>11.0</UNITPRICE>
              <MKTVAL>1100.0</MKTVAL>
              <DTPRICEASOF>20250630000000.000</DTPRICEASOF>
            </INVPOS>
          <POSMF>
            <INVPOS>
              <SECID>
                <UNIQUEID>41150L691</UNIQUEID>
                <UNIQUEIDTYPE>CUSIP</UNIQUEIDTYPE>
              </SECID>
              <UNITS>50.0</UNITS>
              <UNITPRICE>20.0</UNITPRICE>
              <MKTVAL>1000.0</MKTVAL>
              <DTPRICEASOF>20250630000000.000</DTPRICEASOF>
            </INVPOS>
        </INVPOSLIST>
      </INVSTMTRS>
    </INVSTMTTRNRS>
  </INVSTMTMSGSRSV1>
</OFX>
"""


# ── parse_qfx ────────────────────────────────────────────────────────────────

class TestParseQfx:
    def test_fixture_file(self) -> None:
        snap = parse_qfx(FIXTURES_DIR / "qfx_sample.qfx")
        assert snap.date == date(2025, 9, 30)
        assert len(snap.funds) == 2
        assert snap.total == pytest.approx(14100.0)

    def test_fixture_fund_details(self) -> None:
        snap = parse_qfx(FIXTURES_DIR / "qfx_sample.qfx")
        by_cusip = {f.cusip: f for f in snap.funds}
        sp500 = by_cusip["856917729"]
        assert sp500.ticker == "401k sp500"
        assert sp500.shares == pytest.approx(1000.0)
        assert sp500.price == pytest.approx(10.50)
        assert sp500.mktval == pytest.approx(10500.0)

        tech = by_cusip["41150L691"]
        assert tech.ticker == "401k tech"
        assert tech.shares == pytest.approx(200.0)
        assert tech.mktval == pytest.approx(3600.0)

    def test_zero_mktval_fund_skipped(self, tmp_path: Path) -> None:
        """Funds with mktval <= 0 should be excluded."""
        qfx = """\
OFXHEADER:100
DATA:OFXSGML
<OFX><INVSTMTMSGSRSV1><INVSTMTTRNRS><INVSTMTRS>
<DTASOF>20250101000000.000</DTASOF>
<INVTRANLIST><DTSTART>20250101</DTSTART><DTEND>20250101000000.000</DTEND></INVTRANLIST>
<INVPOSLIST>
<POSMF><INVPOS><SECID><UNIQUEID>856917729</UNIQUEID></SECID>
<UNITS>0</UNITS><UNITPRICE>10</UNITPRICE><MKTVAL>0</MKTVAL></INVPOS>
</INVPOSLIST></INVSTMTRS></INVSTMTTRNRS></INVSTMTMSGSRSV1></OFX>
"""
        p = tmp_path / "zero.qfx"
        p.write_text(qfx, encoding="ascii")
        snap = parse_qfx(p)
        assert snap.funds == []
        assert snap.total == 0.0

    def test_unknown_cusip(self, tmp_path: Path) -> None:
        """Unknown CUSIP gets a generated ticker name."""
        qfx = """\
OFXHEADER:100
DATA:OFXSGML
<OFX><INVSTMTMSGSRSV1><INVSTMTTRNRS><INVSTMTRS>
<DTASOF>20250101000000.000</DTASOF>
<INVTRANLIST><DTSTART>20250101</DTSTART><DTEND>20250101000000.000</DTEND></INVTRANLIST>
<INVPOSLIST>
<POSMF><INVPOS><SECID><UNIQUEID>999999999</UNIQUEID></SECID>
<UNITS>10</UNITS><UNITPRICE>5</UNITPRICE><MKTVAL>50</MKTVAL></INVPOS>
</INVPOSLIST></INVSTMTRS></INVSTMTTRNRS></INVSTMTMSGSRSV1></OFX>
"""
        p = tmp_path / "unknown.qfx"
        p.write_text(qfx, encoding="ascii")
        snap = parse_qfx(p)
        assert len(snap.funds) == 1
        assert snap.funds[0].ticker == "401k_unknown_999999999"


# ── parse_qfx_contributions ─────────────────────────────────────────────────

class TestParseQfxContributions:
    def test_extract_buymf_transactions(self, tmp_path: Path) -> None:
        p = tmp_path / "contrib.qfx"
        p.write_text(QFX_WITH_BUYMF, encoding="ascii")
        contribs = parse_qfx_contributions(p)
        assert len(contribs) == 2
        # First contribution
        assert contribs[0].date == date(2025, 4, 15)
        assert contribs[0].amount == pytest.approx(50.0)
        assert contribs[0].ticker == "401k sp500"
        # Second contribution
        assert contribs[1].date == date(2025, 5, 1)
        assert contribs[1].amount == pytest.approx(60.0)
        assert contribs[1].ticker == "401k tech"

    def test_no_buymf_returns_empty(self) -> None:
        """Fixture file has no BUYMF transactions."""
        contribs = parse_qfx_contributions(FIXTURES_DIR / "qfx_sample.qfx")
        assert contribs == []

    def test_negative_total_becomes_positive(self, tmp_path: Path) -> None:
        """TOTAL values are negative in QFX; amount should be abs()."""
        p = tmp_path / "neg.qfx"
        p.write_text(QFX_WITH_BUYMF, encoding="ascii")
        contribs = parse_qfx_contributions(p)
        assert all(c.amount > 0 for c in contribs)


# ── load_all_qfx ────────────────────────────────────────────────────────────

class TestLoadAllQfx:
    def test_single_file(self, tmp_path: Path) -> None:
        import shutil
        shutil.copy(FIXTURES_DIR / "qfx_sample.qfx", tmp_path / "Bloomberg.Download.2025Q3.qfx")
        snapshots = load_all_qfx(tmp_path)
        assert len(snapshots) == 1
        assert snapshots[0].date == date(2025, 9, 30)

    def test_multiple_files_sorted(self, tmp_path: Path) -> None:
        """Two QFX files with different dates should be sorted by date."""
        qfx_q2 = QFX_WITH_BUYMF  # DTEND = 20250630
        qfx_q3 = (FIXTURES_DIR / "qfx_sample.qfx").read_text()  # DTEND = 20250930
        (tmp_path / "Bloomberg.Download.2025Q2.qfx").write_text(qfx_q2, encoding="ascii")
        (tmp_path / "Bloomberg.Download.2025Q3.qfx").write_text(qfx_q3, encoding="ascii")
        snapshots = load_all_qfx(tmp_path)
        assert len(snapshots) == 2
        assert snapshots[0].date < snapshots[1].date

    def test_deduplicates_same_date(self, tmp_path: Path) -> None:
        """Two files with the same date should be deduped (keep last)."""
        qfx = (FIXTURES_DIR / "qfx_sample.qfx").read_text()
        (tmp_path / "Bloomberg.Download.A.qfx").write_text(qfx, encoding="ascii")
        (tmp_path / "Bloomberg.Download.B.qfx").write_text(qfx, encoding="ascii")
        snapshots = load_all_qfx(tmp_path)
        assert len(snapshots) == 1

    def test_empty_directory(self, tmp_path: Path) -> None:
        snapshots = load_all_qfx(tmp_path)
        assert snapshots == []

    def test_skips_empty_snapshots(self, tmp_path: Path) -> None:
        """QFX file with zero-value funds should be skipped."""
        qfx = """\
OFXHEADER:100
DATA:OFXSGML
<OFX><INVSTMTMSGSRSV1><INVSTMTTRNRS><INVSTMTRS>
<DTASOF>20250101000000.000</DTASOF>
<INVTRANLIST><DTSTART>20250101</DTSTART><DTEND>20250101000000.000</DTEND></INVTRANLIST>
<INVPOSLIST>
<POSMF><INVPOS><SECID><UNIQUEID>856917729</UNIQUEID></SECID>
<UNITS>0</UNITS><UNITPRICE>0</UNITPRICE><MKTVAL>0</MKTVAL></INVPOS>
</INVPOSLIST></INVSTMTRS></INVSTMTTRNRS></INVSTMTMSGSRSV1></OFX>
"""
        (tmp_path / "Bloomberg.Download.empty.qfx").write_text(qfx, encoding="ascii")
        snapshots = load_all_qfx(tmp_path)
        assert snapshots == []


# ── load_all_contributions ───────────────────────────────────────────────────

class TestLoadAllContributions:
    def test_loads_and_deduplicates(self, tmp_path: Path) -> None:
        """Same file copied twice should yield no duplicates."""
        (tmp_path / "Bloomberg.Download.A.qfx").write_text(QFX_WITH_BUYMF, encoding="ascii")
        (tmp_path / "Bloomberg.Download.B.qfx").write_text(QFX_WITH_BUYMF, encoding="ascii")
        contribs = load_all_contributions(tmp_path)
        # Two unique contributions (different dates/amounts)
        assert len(contribs) == 2

    def test_sorted_by_date(self, tmp_path: Path) -> None:
        (tmp_path / "Bloomberg.Download.X.qfx").write_text(QFX_WITH_BUYMF, encoding="ascii")
        contribs = load_all_contributions(tmp_path)
        dates = [c.date for c in contribs]
        assert dates == sorted(dates)

    def test_empty_directory(self, tmp_path: Path) -> None:
        contribs = load_all_contributions(tmp_path)
        assert contribs == []


# ── _ffill_proxy ─────────────────────────────────────────────────────────────

class TestFfillProxy:
    def test_exact_date(self) -> None:
        prices = {date(2025, 1, 2): 100.0}
        assert _ffill_proxy(prices, date(2025, 1, 2)) == 100.0

    def test_weekend_fill(self) -> None:
        """Saturday should fill back to Friday."""
        prices = {date(2025, 1, 3): 100.0}  # Friday
        assert _ffill_proxy(prices, date(2025, 1, 4)) == 100.0  # Saturday

    def test_too_far_back_returns_none(self) -> None:
        """More than 7 days gap returns None."""
        prices = {date(2025, 1, 1): 100.0}
        assert _ffill_proxy(prices, date(2025, 1, 10)) is None

    def test_empty_prices(self) -> None:
        assert _ffill_proxy({}, date(2025, 1, 2)) is None


# ── daily_401k_values ────────────────────────────────────────────────────────

class TestDaily401kValues:
    """Test interpolation of daily 401k values between quarterly snapshots."""

    @staticmethod
    def _make_snapshot(d: date, funds: list[tuple[str, float]]) -> QuarterSnapshot:
        """Create a snapshot with funds specified as (ticker, mktval) pairs."""
        fund_list = [
            FundSnapshot(date=d, cusip="", ticker=ticker, shares=0, price=0, mktval=mktval)
            for ticker, mktval in funds
        ]
        return QuarterSnapshot(date=d, funds=fund_list, total=sum(v for _, v in funds))

    def test_empty_snapshots(self) -> None:
        result = daily_401k_values([], {}, date(2025, 1, 1), date(2025, 1, 5))
        assert result == {}

    def test_single_snapshot_no_proxy_change(self) -> None:
        """When proxy price is flat, value stays at snapshot level."""
        snap = self._make_snapshot(date(2025, 1, 1), [("401k sp500", 10000.0)])
        proxy = {"VOO": {date(2025, 1, 1): 500.0, date(2025, 1, 2): 500.0, date(2025, 1, 3): 500.0}}
        result = daily_401k_values([snap], proxy, date(2025, 1, 1), date(2025, 1, 3))
        for d in [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)]:
            assert result[d]["401k sp500"] == pytest.approx(10000.0)

    def test_proxy_scaling(self) -> None:
        """Value should scale proportionally to proxy price change."""
        snap = self._make_snapshot(date(2025, 1, 1), [("401k sp500", 10000.0)])
        proxy = {"VOO": {date(2025, 1, 1): 100.0, date(2025, 1, 2): 110.0}}
        result = daily_401k_values([snap], proxy, date(2025, 1, 1), date(2025, 1, 2))
        # Day 1: base value
        assert result[date(2025, 1, 1)]["401k sp500"] == pytest.approx(10000.0)
        # Day 2: 10% gain in proxy → 10% gain in value
        assert result[date(2025, 1, 2)]["401k sp500"] == pytest.approx(11000.0)

    def test_two_snapshots_two_funds(self) -> None:
        """Second snapshot resets the base; multiple funds interpolate independently."""
        snap1 = self._make_snapshot(date(2025, 1, 1), [
            ("401k sp500", 10000.0),
            ("401k tech", 5000.0),
        ])
        snap2 = self._make_snapshot(date(2025, 4, 1), [
            ("401k sp500", 12000.0),
            ("401k tech", 6000.0),
        ])
        proxy = {
            "VOO": {
                date(2025, 1, 1): 100.0,
                date(2025, 2, 1): 105.0,
                date(2025, 4, 1): 110.0,
                date(2025, 5, 1): 115.0,
            },
            "QQQM": {
                date(2025, 1, 1): 200.0,
                date(2025, 2, 1): 210.0,
                date(2025, 4, 1): 220.0,
                date(2025, 5, 1): 230.0,
            },
        }
        result = daily_401k_values([snap1, snap2], proxy, date(2025, 2, 1), date(2025, 5, 1))
        # Feb 1: uses snap1, VOO 105/100 = 1.05
        assert result[date(2025, 2, 1)]["401k sp500"] == pytest.approx(10000.0 * 105.0 / 100.0)
        assert result[date(2025, 2, 1)]["401k tech"] == pytest.approx(5000.0 * 210.0 / 200.0)
        # May 1: uses snap2, VOO 115/110
        assert result[date(2025, 5, 1)]["401k sp500"] == pytest.approx(12000.0 * 115.0 / 110.0)
        assert result[date(2025, 5, 1)]["401k tech"] == pytest.approx(6000.0 * 230.0 / 220.0)

    def test_dates_before_first_snapshot_skipped(self) -> None:
        """Dates before the first snapshot produce no values."""
        snap = self._make_snapshot(date(2025, 1, 5), [("401k sp500", 10000.0)])
        proxy = {"VOO": {date(2025, 1, 1): 100.0, date(2025, 1, 5): 100.0}}
        result = daily_401k_values([snap], proxy, date(2025, 1, 1), date(2025, 1, 5))
        assert date(2025, 1, 1) not in result
        assert date(2025, 1, 5) in result

    def test_contribution_compensation(self) -> None:
        """Contributions after snapshot should add value scaled by proxy."""
        snap = self._make_snapshot(date(2025, 1, 1), [("401k sp500", 10000.0)])
        proxy = {"VOO": {date(2025, 1, 1): 100.0, date(2025, 1, 15): 100.0, date(2025, 1, 31): 110.0}}
        contribs = [Contribution(date=date(2025, 1, 15), amount=1000.0, ticker="401k sp500")]
        result = daily_401k_values([snap], proxy, date(2025, 1, 1), date(2025, 1, 31), contributions=contribs)
        # Jan 1: just snapshot, no contribution yet (contrib date > snap date, contrib date > current is True)
        assert result[date(2025, 1, 1)]["401k sp500"] == pytest.approx(10000.0)
        # Jan 31: snapshot scaled + contribution scaled
        # Snapshot: 10000 * 110/100 = 11000
        # Contribution: 1000 * 110/100 = 1100
        assert result[date(2025, 1, 31)]["401k sp500"] == pytest.approx(12100.0)

    def test_contribution_before_current_only(self) -> None:
        """Contributions after the current date should not be counted."""
        snap = self._make_snapshot(date(2025, 1, 1), [("401k sp500", 10000.0)])
        proxy = {"VOO": {date(2025, 1, 1): 100.0, date(2025, 1, 10): 100.0, date(2025, 1, 20): 100.0}}
        contribs = [Contribution(date=date(2025, 1, 20), amount=500.0, ticker="401k sp500")]
        result = daily_401k_values([snap], proxy, date(2025, 1, 1), date(2025, 1, 10), contributions=contribs)
        # Jan 10: contribution date (Jan 20) > current (Jan 10), so not included
        assert result[date(2025, 1, 10)]["401k sp500"] == pytest.approx(10000.0)

    def test_no_proxy_ticker_uses_raw_mktval(self) -> None:
        """Fund with unknown proxy should use raw mktval."""
        snap = self._make_snapshot(date(2025, 1, 1), [("401k_unknown_XYZ", 5000.0)])
        result = daily_401k_values([snap], {}, date(2025, 1, 1), date(2025, 1, 2))
        assert result[date(2025, 1, 1)]["401k_unknown_XYZ"] == pytest.approx(5000.0)
        assert result[date(2025, 1, 2)]["401k_unknown_XYZ"] == pytest.approx(5000.0)

    def test_missing_proxy_price_uses_raw_mktval(self) -> None:
        """If proxy has no data at all, fall back to snapshot mktval."""
        snap = self._make_snapshot(date(2025, 1, 1), [("401k sp500", 8000.0)])
        # VOO proxy exists but has no prices
        result = daily_401k_values([snap], {"VOO": {}}, date(2025, 1, 1), date(2025, 1, 2))
        assert result[date(2025, 1, 1)]["401k sp500"] == pytest.approx(8000.0)
