"""Unit tests for the Empower source module (post class→module refactor)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import etl.sources.empower as empower_src
from etl.sources._types import PriceContext
from tests.fixtures import connected_db, db_rows, db_value


def _seed_empower(
    db_path: Path,
    rows: list[tuple[str, str, str, float, float, float]],
) -> None:
    """Seed empower_snapshots + empower_funds.

    Each row: (snapshot_date, cusip, ticker, shares, price, mktval).
    """
    with connected_db(db_path) as conn:
        snap_ids: dict[str, int] = {}
        for snap_date, cusip, ticker, shares, price, mktval in rows:
            if snap_date not in snap_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO empower_snapshots (snapshot_date) VALUES (?)",
                    (snap_date,),
                )
                sid = conn.execute(
                    "SELECT id FROM empower_snapshots WHERE snapshot_date = ?",
                    (snap_date,),
                ).fetchone()[0]
                snap_ids[snap_date] = sid
            conn.execute(
                "INSERT INTO empower_funds (snapshot_id, cusip, ticker, shares, price, mktval)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (snap_ids[snap_date], cusip, ticker, shares, price, mktval),
            )


def test_positions_at_returns_latest_snapshot_at_or_before(empty_db: Path) -> None:
    """The latest snapshot <= as_of is used; later snapshots are ignored."""
    _seed_empower(empty_db, [
        ("2024-06-30", "SSgAxxx", "401k sp500", 100.0, 25.0, 2500.0),
        ("2024-12-31", "SSgAxxx", "401k sp500", 120.0, 28.0, 3360.0),
    ])

    # No VOO prices → falls back to raw mktval.
    ctx = PriceContext(prices=pd.DataFrame(), price_date=date(2024, 8, 1), mf_price_date=date(2024, 8, 1))

    # August → latest ≤ Aug 1 is June snapshot (raw mktval = 2500.0).
    rows = empower_src.positions_at(empty_db, date(2024, 8, 1), ctx, {})
    assert any(r.ticker == "401k sp500" and r.value_usd == pytest.approx(2500.0) for r in rows)


def test_positions_at_before_first_snapshot_returns_empty(empty_db: Path) -> None:
    """No snapshot at-or-before as_of → empty list (not an error)."""
    _seed_empower(empty_db, [
        ("2024-06-30", "SSgAxxx", "401k sp500", 100.0, 25.0, 2500.0),
    ])

    ctx = PriceContext(prices=pd.DataFrame(), price_date=date(2024, 1, 1), mf_price_date=date(2024, 1, 1))
    rows = empower_src.positions_at(empty_db, date(2024, 1, 1), ctx, {})
    assert rows == []


def test_cost_basis_is_none(empty_db: Path) -> None:
    """Spec: Empower positions leave cost_basis_usd=None (QFX doesn't carry it)."""
    _seed_empower(empty_db, [
        ("2024-06-30", "SSgAxxx", "401k sp500", 100.0, 25.0, 2500.0),
    ])

    ctx = PriceContext(prices=pd.DataFrame(), price_date=date(2024, 8, 1), mf_price_date=date(2024, 8, 1))
    rows = empower_src.positions_at(empty_db, date(2024, 8, 1), ctx, {})
    assert rows and all(r.cost_basis_usd is None for r in rows)


def test_positions_at_scales_by_proxy_prices(empty_db: Path) -> None:
    """Value between snapshots scales proportionally to proxy ticker change."""
    _seed_empower(empty_db, [
        ("2024-06-30", "SSgAxxx", "401k sp500", 100.0, 25.0, 2500.0),
    ])
    # Proxy VOO prices: June 30 = 100.0, Aug 1 = 110.0
    prices_df = pd.DataFrame(
        {"VOO": [100.0, 110.0]},
        index=[date(2024, 6, 30), date(2024, 8, 1)],
    )
    ctx = PriceContext(prices=prices_df, price_date=date(2024, 8, 1), mf_price_date=date(2024, 8, 1))

    rows = empower_src.positions_at(empty_db, date(2024, 8, 1), ctx, {})
    sp500 = [r for r in rows if r.ticker == "401k sp500"]
    assert len(sp500) == 1
    assert sp500[0].value_usd == pytest.approx(2750.0)


def test_contributions_add_scaled_amount(empty_db: Path) -> None:
    """Contributions after snapshot date are added, scaled by proxy."""
    _seed_empower(empty_db, [
        ("2024-06-30", "SSgAxxx", "401k sp500", 100.0, 25.0, 10000.0),
    ])
    # Contribution of $1000 on Jul 15 at VOO=100 → worth 1000 * (110/100) = 1100 on Aug 1.
    with connected_db(empty_db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO empower_contributions (date, amount, ticker, cusip) VALUES (?, ?, ?, ?)",
            ("2024-07-15", 1000.0, "401k sp500", ""),
        )

    prices_df = pd.DataFrame(
        {"VOO": [100.0, 100.0, 110.0]},
        index=[date(2024, 6, 30), date(2024, 7, 15), date(2024, 8, 1)],
    )
    ctx = PriceContext(prices=prices_df, price_date=date(2024, 8, 1), mf_price_date=date(2024, 8, 1))

    rows = empower_src.positions_at(empty_db, date(2024, 8, 1), ctx, {})
    sp500 = [r for r in rows if r.ticker == "401k sp500"]
    # Snapshot 10000 * (110/100) = 11000 + contribution 1000 * (110/100) = 1100 → 12100
    assert sp500[0].value_usd == pytest.approx(12100.0)


def test_ingest_writes_snapshot_and_contributions(tmp_path: Path, empty_db: Path) -> None:
    """ingest() scans directory for Bloomberg.Download*.qfx and populates tables."""
    qfx_content = """\
OFXHEADER:100
DATA:OFXSGML
<OFX><INVSTMTMSGSRSV1><INVSTMTTRNRS><INVSTMTRS>
<DTASOF>20240630000000.000</DTASOF>
<INVTRANLIST><DTSTART>20240401000000.000</DTSTART><DTEND>20240630000000.000</DTEND>
<BUYMF>
  <INVBUY><INVTRAN><FITID>id1</FITID><DTTRADE>20240501</DTTRADE></INVTRAN>
  <SECID><UNIQUEID>856917729</UNIQUEID></SECID>
  <UNITS>10</UNITS><UNITPRICE>10</UNITPRICE><TOTAL>-100.00</TOTAL></INVBUY>
</INVTRANLIST>
<INVPOSLIST>
<POSMF><INVPOS><SECID><UNIQUEID>856917729</UNIQUEID></SECID>
<UNITS>100</UNITS><UNITPRICE>25</UNITPRICE><MKTVAL>2500</MKTVAL></INVPOS>
</INVPOSLIST></INVSTMTRS></INVSTMTTRNRS></INVSTMTMSGSRSV1></OFX>
"""
    (tmp_path / "Bloomberg.Download.2024Q2.qfx").write_text(qfx_content, encoding="ascii")

    empower_src.ingest(empty_db, tmp_path)

    assert db_value(empty_db, "SELECT COUNT(*) FROM empower_snapshots") == 1
    assert db_value(empty_db, "SELECT COUNT(*) FROM empower_funds") == 1
    assert db_value(empty_db, "SELECT COUNT(*) FROM empower_contributions") == 1


def test_ingest_skips_zero_mktval_funds(tmp_path: Path, empty_db: Path) -> None:
    """Funds with mktval <= 0 or units <= 0 are excluded at parse time."""
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
    (tmp_path / "Bloomberg.Download.zero.qfx").write_text(qfx, encoding="ascii")
    empower_src.ingest(empty_db, tmp_path)

    assert db_value(empty_db, "SELECT COUNT(*) FROM empower_funds") == 0


def test_ingest_unknown_cusip_uses_fallback_ticker(tmp_path: Path, empty_db: Path) -> None:
    """Unknown CUSIP → ``401k_unknown_<cusip>`` fallback ticker."""
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
    (tmp_path / "Bloomberg.Download.unknown.qfx").write_text(qfx, encoding="ascii")
    empower_src.ingest(empty_db, tmp_path)

    tickers = [row[0] for row in db_rows(empty_db, "SELECT ticker FROM empower_funds")]
    assert tickers == ["401k_unknown_999999999"]


def test_ingest_is_idempotent(tmp_path: Path, empty_db: Path) -> None:
    """Running ingest() twice is a no-op: same snapshot count + same fund count."""
    qfx = """\
OFXHEADER:100
DATA:OFXSGML
<OFX><INVSTMTMSGSRSV1><INVSTMTTRNRS><INVSTMTRS>
<DTASOF>20240630000000.000</DTASOF>
<INVTRANLIST><DTSTART>20240101</DTSTART><DTEND>20240630000000.000</DTEND></INVTRANLIST>
<INVPOSLIST>
<POSMF><INVPOS><SECID><UNIQUEID>856917729</UNIQUEID></SECID>
<UNITS>100</UNITS><UNITPRICE>25</UNITPRICE><MKTVAL>2500</MKTVAL></INVPOS>
</INVPOSLIST></INVSTMTRS></INVSTMTTRNRS></INVSTMTMSGSRSV1></OFX>
"""
    (tmp_path / "Bloomberg.Download.A.qfx").write_text(qfx, encoding="ascii")
    cfg: dict[str, object] = {"empower_cusip_map": {"999999999": "401k custom"}}
    empower_src.ingest(empty_db, tmp_path, cfg)
    empower_src.ingest(empty_db, tmp_path, cfg)
    assert db_value(empty_db, "SELECT COUNT(*) FROM empower_snapshots") == 1
    assert db_value(empty_db, "SELECT COUNT(*) FROM empower_funds") == 1


def test_ingest_missing_dir_is_noop(tmp_path: Path, empty_db: Path) -> None:
    """Non-existent downloads directory → silent no-op."""
    empower_src.ingest(empty_db, tmp_path / "does_not_exist")
    assert db_value(empty_db, "SELECT COUNT(*) FROM empower_snapshots") == 0


class TestIngestContributionsReconcile:
    """QFX vs Qianji fallback reconciliation in ``ingest_contributions``.

    Qianji fallback rows (cusip='') fill dates without QFX coverage. When a
    QFX ingest finally covers one of those dates, ``ingest_contributions``
    cross-checks the per-date totals before dropping the now-redundant
    fallback rows. A mismatch means the two sources disagree on what
    happened — abort the build (fail-loud) rather than silently
    double-count or silently prefer one side.
    """

    @staticmethod
    def _seed_fallback(db_path: Path, rows: list[tuple[str, float, str]]) -> None:
        """Insert Qianji fallback rows (cusip='') directly."""
        with connected_db(db_path) as conn:
            conn.executemany(
                "INSERT INTO empower_contributions (date, amount, ticker, cusip) "
                "VALUES (?, ?, ?, '')",
                rows,
            )

    @staticmethod
    def _all_contribs(db_path: Path) -> list[tuple]:
        return db_rows(db_path, "SELECT date, amount, ticker, cusip FROM empower_contributions ORDER BY date, ticker, cusip")

    def test_qfx_only_date_inserts_cleanly(self, empty_db: Path) -> None:
        """No pre-existing fallback → straight insert, no reconcile."""
        contribs = [
            empower_src.Contribution(date(2026, 4, 15), 820.31, "401k ex-us", "85744W531"),
            empower_src.Contribution(date(2026, 4, 15), 820.32, "401k sp500", "856917729"),
        ]
        empower_src.ingest_contributions(empty_db, contribs)
        rows = self._all_contribs(empty_db)
        assert rows == [
            ("2026-04-15", 820.31, "401k ex-us", "85744W531"),
            ("2026-04-15", 820.32, "401k sp500", "856917729"),
        ]

    def test_matching_totals_delete_fallback_then_insert(self, empty_db: Path) -> None:
        """QFX total == Qianji fallback total → fallback rows dropped, QFX wins."""
        # Pre-existing Qianji fallback: 820.315 × 2 tickers = $1640.63
        self._seed_fallback(empty_db, [
            ("2026-04-15", 820.315, "401k sp500"),
            ("2026-04-15", 820.315, "401k ex-us"),
        ])
        # QFX arrives with matching $1640.63 total (820.31 + 820.32)
        contribs = [
            empower_src.Contribution(date(2026, 4, 15), 820.31, "401k ex-us", "85744W531"),
            empower_src.Contribution(date(2026, 4, 15), 820.32, "401k sp500", "856917729"),
        ]
        empower_src.ingest_contributions(empty_db, contribs)
        rows = self._all_contribs(empty_db)
        # Only the QFX rows survive — fallback got cleaned up.
        assert rows == [
            ("2026-04-15", 820.31, "401k ex-us", "85744W531"),
            ("2026-04-15", 820.32, "401k sp500", "856917729"),
        ]

    def test_mismatched_totals_raise(self, empty_db: Path) -> None:
        """QFX total differs from fallback total by > tolerance → abort build."""
        # Fallback says $1000 total; QFX says $1640.63 — drift of ~$640, way over $1 tolerance.
        self._seed_fallback(empty_db, [
            ("2026-04-15", 500.00, "401k sp500"),
            ("2026-04-15", 500.00, "401k ex-us"),
        ])
        contribs = [
            empower_src.Contribution(date(2026, 4, 15), 820.31, "401k ex-us", "85744W531"),
            empower_src.Contribution(date(2026, 4, 15), 820.32, "401k sp500", "856917729"),
        ]
        with pytest.raises(empower_src.ContributionReconcileError, match=r"QFX total=\$1640\.63.*Qianji fallback=\$1000\.00"):
            empower_src.ingest_contributions(empty_db, contribs)
        # On failure nothing should be persisted or deleted — leave DB as-is for forensics.
        rows = self._all_contribs(empty_db)
        assert rows == [
            ("2026-04-15", 500.0, "401k ex-us", ""),
            ("2026-04-15", 500.0, "401k sp500", ""),
        ]

    def test_sub_dollar_drift_within_tolerance_passes(self, empty_db: Path) -> None:
        """Qianji's 50/50 split can drift a cent or two from the real QFX
        allocation; that's the exact kind of noise the $1 tolerance exists
        to absorb."""
        # Fallback 820.315 × 2 = $1640.63; QFX 820.80 + 819.90 = $1640.70 (diff $0.07)
        self._seed_fallback(empty_db, [
            ("2026-04-15", 820.315, "401k sp500"),
            ("2026-04-15", 820.315, "401k ex-us"),
        ])
        contribs = [
            empower_src.Contribution(date(2026, 4, 15), 820.80, "401k sp500", "856917729"),
            empower_src.Contribution(date(2026, 4, 15), 819.90, "401k ex-us", "85744W531"),
        ]
        empower_src.ingest_contributions(empty_db, contribs)
        rows = self._all_contribs(empty_db)
        # Fallback gone, QFX lands.
        assert [r[3] for r in rows] == ["85744W531", "856917729"]
        assert sum(r[1] for r in rows) == pytest.approx(1640.70, rel=1e-6)

    def test_qianji_only_write_does_not_trigger_reconcile(self, empty_db: Path) -> None:
        """Ingesting Qianji fallback alone (all rows cusip='') must not
        reconcile against itself or delete anything — the reconcile is only
        triggered by the QFX-side ingest."""
        self._seed_fallback(empty_db, [
            ("2026-01-15", 1287.5, "401k sp500"),
            ("2026-01-15", 1287.5, "401k ex-us"),
        ])
        # Now ingest a second Qianji fallback batch (e.g., next build cycle)
        contribs = [
            empower_src.Contribution(date(2026, 2, 13), 1287.5, "401k sp500", ""),
            empower_src.Contribution(date(2026, 2, 13), 1287.5, "401k ex-us", ""),
        ]
        empower_src.ingest_contributions(empty_db, contribs)
        rows = self._all_contribs(empty_db)
        # Jan rows untouched; Feb rows added.
        assert len(rows) == 4
        assert all(r[3] == "" for r in rows)
