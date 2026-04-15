"""Unit tests for EmpowerSource (Phase 5 — Task 20)."""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etl.db import init_db
from etl.sources import PriceContext, SourceKind
from etl.sources.empower import EmpowerSource, EmpowerSourceConfig


def _seed_empower(
    db_path: Path,
    rows: list[tuple[str, str, str, float, float, float]],
) -> None:
    """Seed empower_snapshots + empower_funds.

    Each row: (snapshot_date, cusip, ticker, shares, price, mktval).
    """
    conn = sqlite3.connect(str(db_path))
    try:
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
        conn.commit()
    finally:
        conn.close()


def test_kind() -> None:
    assert EmpowerSource.kind == SourceKind.EMPOWER


def test_positions_at_returns_latest_snapshot_at_or_before(tmp_path: Path) -> None:
    """The latest snapshot <= as_of is used; later snapshots are ignored."""
    db = tmp_path / "tm.db"
    init_db(db)
    _seed_empower(db, [
        ("2024-06-30", "SSgAxxx", "401k sp500", 100.0, 25.0, 2500.0),
        ("2024-12-31", "SSgAxxx", "401k sp500", 120.0, 28.0, 3360.0),
    ])

    src = EmpowerSource(EmpowerSourceConfig(downloads_dir=tmp_path), db)
    # No VOO prices → falls back to raw mktval.
    ctx = PriceContext(prices=pd.DataFrame(), price_date=date(2024, 8, 1), mf_price_date=date(2024, 8, 1))

    # August → latest ≤ Aug 1 is June snapshot (raw mktval = 2500.0).
    rows = src.positions_at(date(2024, 8, 1), ctx)
    assert any(r.ticker == "401k sp500" and r.value_usd == pytest.approx(2500.0) for r in rows)


def test_positions_at_before_first_snapshot_returns_empty(tmp_path: Path) -> None:
    """No snapshot at-or-before as_of → empty list (not an error)."""
    db = tmp_path / "tm.db"
    init_db(db)
    _seed_empower(db, [
        ("2024-06-30", "SSgAxxx", "401k sp500", 100.0, 25.0, 2500.0),
    ])

    src = EmpowerSource(EmpowerSourceConfig(downloads_dir=tmp_path), db)
    ctx = PriceContext(prices=pd.DataFrame(), price_date=date(2024, 1, 1), mf_price_date=date(2024, 1, 1))

    rows = src.positions_at(date(2024, 1, 1), ctx)
    assert rows == []


def test_cost_basis_is_none(tmp_path: Path) -> None:
    """Spec: EmpowerSource MAY leave cost_basis_usd=None (QFX doesn't carry it)."""
    db = tmp_path / "tm.db"
    init_db(db)
    _seed_empower(db, [
        ("2024-06-30", "SSgAxxx", "401k sp500", 100.0, 25.0, 2500.0),
    ])

    src = EmpowerSource(EmpowerSourceConfig(downloads_dir=tmp_path), db)
    ctx = PriceContext(prices=pd.DataFrame(), price_date=date(2024, 8, 1), mf_price_date=date(2024, 8, 1))

    rows = src.positions_at(date(2024, 8, 1), ctx)
    assert rows and all(r.cost_basis_usd is None for r in rows)


def test_positions_at_scales_by_proxy_prices(tmp_path: Path) -> None:
    """Value between snapshots scales proportionally to proxy ticker change.

    Snapshot on Jun 30 with VOO at $100; as-of Aug 1 with VOO at $110 →
    value = mktval * (110/100) = 2750.
    """
    db = tmp_path / "tm.db"
    init_db(db)
    _seed_empower(db, [
        ("2024-06-30", "SSgAxxx", "401k sp500", 100.0, 25.0, 2500.0),
    ])
    # Proxy VOO prices: June 30 = 100.0, Aug 1 = 110.0
    prices_df = pd.DataFrame(
        {"VOO": [100.0, 110.0]},
        index=[date(2024, 6, 30), date(2024, 8, 1)],
    )
    src = EmpowerSource(EmpowerSourceConfig(downloads_dir=tmp_path), db)
    ctx = PriceContext(prices=prices_df, price_date=date(2024, 8, 1), mf_price_date=date(2024, 8, 1))

    rows = src.positions_at(date(2024, 8, 1), ctx)
    sp500 = [r for r in rows if r.ticker == "401k sp500"]
    assert len(sp500) == 1
    assert sp500[0].value_usd == pytest.approx(2750.0)


def test_contributions_add_scaled_amount(tmp_path: Path) -> None:
    """Contributions after snapshot date are added, scaled by proxy."""
    db = tmp_path / "tm.db"
    init_db(db)
    _seed_empower(db, [
        ("2024-06-30", "SSgAxxx", "401k sp500", 100.0, 25.0, 10000.0),
    ])
    # Contribution of $1000 on Jul 15 at VOO=100 → worth 1000 * (110/100) = 1100 on Aug 1.
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT OR REPLACE INTO empower_contributions (date, amount, ticker, cusip) VALUES (?, ?, ?, ?)",
        ("2024-07-15", 1000.0, "401k sp500", ""),
    )
    conn.commit()
    conn.close()

    prices_df = pd.DataFrame(
        {"VOO": [100.0, 100.0, 110.0]},
        index=[date(2024, 6, 30), date(2024, 7, 15), date(2024, 8, 1)],
    )
    src = EmpowerSource(EmpowerSourceConfig(downloads_dir=tmp_path), db)
    ctx = PriceContext(prices=prices_df, price_date=date(2024, 8, 1), mf_price_date=date(2024, 8, 1))

    rows = src.positions_at(date(2024, 8, 1), ctx)
    sp500 = [r for r in rows if r.ticker == "401k sp500"]
    # Snapshot 10000 * (110/100) = 11000 + contribution 1000 * (110/100) = 1100 → 12100
    assert sp500[0].value_usd == pytest.approx(12100.0)


def test_from_raw_config_reads_downloads_key(tmp_path: Path) -> None:
    raw = {"empower_downloads": tmp_path}
    src = EmpowerSource.from_raw_config(raw, tmp_path / "tm.db")
    assert src._config.downloads_dir == tmp_path


def test_ingest_writes_snapshot_and_contributions(tmp_path: Path) -> None:
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

    db = tmp_path / "tm.db"
    init_db(db)
    src = EmpowerSource(EmpowerSourceConfig(downloads_dir=tmp_path), db)
    src.ingest()

    conn = sqlite3.connect(str(db))
    try:
        n_snaps = conn.execute("SELECT COUNT(*) FROM empower_snapshots").fetchone()[0]
        n_funds = conn.execute("SELECT COUNT(*) FROM empower_funds").fetchone()[0]
        n_contribs = conn.execute("SELECT COUNT(*) FROM empower_contributions").fetchone()[0]
    finally:
        conn.close()

    assert n_snaps == 1
    assert n_funds == 1
    assert n_contribs == 1
