"""Empower source — snapshot-level 401k broker.

Owns:
  - QFX parsing (``Bloomberg.Download*.qfx`` files from Empower's export).
  - Ingest into ``empower_snapshots`` + ``empower_funds`` + ``empower_contributions``.
  - Per-day position lookup: latest snapshot at-or-before ``as_of``, with
    per-fund value scaled by proxy-ticker returns and augmented by any
    contributions made between the snapshot date and ``as_of``. The proxy
    interpolation mirrors the legacy ``etl.k401.daily_401k_values`` so that
    migrated output matches the pre-refactor baseline row-for-row.

QFX files don't carry cost basis, so every ``PositionRow`` here returns
``cost_basis_usd=None`` — the spec-documented Empower invariant.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from etl.db import get_connection
from etl.sources import PositionRow, PriceContext

# ── CUSIP → config ticker → proxy ticker mapping ───────────────────────────


_DEFAULT_CUSIP_MAP: dict[str, str] = {
    # S&P 500
    "85744W705": "401k sp500",   # SSgA S&P 500 Index Fund Class K
    "856917729": "401k sp500",   # State Street S&P 500A Index Non Lend M
    # Growth / Tech
    "41150L402": "401k tech",    # Harbor Capital Appreciation CIT 4
    "41150L691": "401k tech",    # Harbor Capital Appreciation R
    # ex-US
    "233203421": "401k ex-us",   # DFA Emerging Market Core Equity 2 Port I
    "85744W531": "401k ex-us",   # SSgA Global All Cap Equity ex-US Index K
    # Target date (approximate as S&P 500)
    "09259A791": "401k sp500",   # LifePath Index 2060 Non-Lendable M
}

PROXY_TICKERS: dict[str, str] = {
    "401k sp500": "VOO",
    "401k tech": "QQQM",
    "401k ex-us": "VXUS",
}


# ── Parsed-QFX records (kept internal to this module) ──────────────────────


@dataclass(frozen=True)
class FundSnapshot:
    """Single fund position at a point in time (post-parse, pre-DB)."""
    date: date
    cusip: str
    ticker: str
    shares: float
    price: float
    mktval: float


@dataclass(frozen=True)
class QuarterSnapshot:
    """All fund positions at quarter end."""
    date: date
    funds: list[FundSnapshot]


@dataclass(frozen=True)
class Contribution:
    """A 401k contribution (BUYMF from QFX, or Qianji fallback)."""
    date: date
    amount: float
    ticker: str
    cusip: str = ""


# ── QFX parsing ────────────────────────────────────────────────────────────


def _extract_tag(text: str, tag: str) -> str:
    """Extract value between ``<TAG>value`` (OFX shorthand, no closing tag)."""
    m = re.search(rf"<{tag}>\s*([^<\s]+)", text)
    return m.group(1).strip() if m else ""


def _parse_qfx(path: Path, cusip_map: dict[str, str]) -> QuarterSnapshot:
    """Parse a single Empower QFX file into a :class:`QuarterSnapshot`."""
    text = path.read_text(encoding="ascii", errors="replace")
    dt_end = _extract_tag(text, "DTEND")
    snap_date = datetime.strptime(dt_end[:8], "%Y%m%d").date()

    funds: list[FundSnapshot] = []
    for m in re.finditer(r"<POSMF>(.*?)(?=<POSMF>|</INVPOSLIST>|$)", text, re.DOTALL):
        block = m.group(1)
        cusip = _extract_tag(block, "UNIQUEID")
        units = float(_extract_tag(block, "UNITS") or "0")
        price = float(_extract_tag(block, "UNITPRICE") or "0")
        mktval = float(_extract_tag(block, "MKTVAL") or "0")
        if mktval <= 0 or units <= 0:
            continue
        ticker = cusip_map.get(cusip, f"401k_unknown_{cusip}")
        funds.append(FundSnapshot(
            date=snap_date, cusip=cusip, ticker=ticker,
            shares=units, price=price, mktval=mktval,
        ))
    return QuarterSnapshot(date=snap_date, funds=funds)


def _parse_qfx_contributions(path: Path, cusip_map: dict[str, str]) -> list[Contribution]:
    """Extract BUYMF transactions from a QFX file as contributions."""
    text = path.read_text(encoding="ascii", errors="replace")
    contribs: list[Contribution] = []
    for m in re.finditer(r"<BUYMF>(.*?)(?=<BUYMF>|</INVTRANLIST>|<INVPOSLIST>)", text, re.DOTALL):
        block = m.group(1)
        dt_m = re.search(r"<DTTRADE>(\d{8})", block)
        total_m = re.search(r"<TOTAL>([^<\s]+)", block)
        cusip_m = re.search(r"<UNIQUEID>([^<\s]+)", block)
        if not dt_m or not total_m:
            continue
        d = datetime.strptime(dt_m.group(1), "%Y%m%d").date()
        amount = abs(float(total_m.group(1)))
        cusip = cusip_m.group(1) if cusip_m else ""
        ticker = cusip_map.get(cusip, "401k sp500")
        contribs.append(Contribution(date=d, amount=amount, ticker=ticker, cusip=cusip))
    return contribs


def _ffill_proxy(prices: dict[date, float], d: date) -> float | None:
    """Find most recent proxy price on or before ``d`` (up to 7 days back)."""
    for i in range(8):
        p = prices.get(d - timedelta(days=i))
        if p is not None:
            return p
    return None


def _proxy_prices_from_df(prices: pd.DataFrame, proxy: str) -> dict[date, float]:
    """Extract a ``{date: close}`` map for a proxy ticker from the PriceContext frame."""
    if prices.empty or proxy not in prices.columns:
        return {}
    series = prices[proxy].dropna()
    return {d: float(series.loc[d]) for d in series.index}


# ── Config helpers ─────────────────────────────────────────────────────────


def _downloads_dir(config: dict[str, object]) -> Path:
    raw = config.get("empower_downloads")
    if isinstance(raw, (str, Path)):
        return Path(raw)
    return Path("__missing_empower_downloads__")


def _cusip_map(config: dict[str, object]) -> dict[str, str]:
    raw = config.get("empower_cusip_map")
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    return dict(_DEFAULT_CUSIP_MAP)


# ── Public API (module protocol) ───────────────────────────────────────────


def produces_positions(config: dict[str, object]) -> bool:
    """Always on — :func:`positions_at` returns ``[]`` before the first snapshot."""
    del config
    return True


def ingest(db_path: Path, config: dict[str, object]) -> None:
    """Scan ``empower_downloads`` for ``Bloomberg.Download*.qfx`` and ingest each.

    Populates ``empower_snapshots`` + ``empower_funds`` (idempotent per
    snapshot date: INSERT OR IGNORE the snapshot, DELETE + INSERT the funds)
    and ``empower_contributions`` (deduped by
    ``(date, amount, ticker, cusip)``).

    Silent no-op when the downloads directory doesn't exist — mirrors
    :func:`etl.sources.robinhood.ingest`'s missing-CSV behaviour.
    """
    downloads_dir = _downloads_dir(config)
    cusip_map = _cusip_map(config)
    if not downloads_dir.exists():
        return
    qfx_paths = sorted(downloads_dir.glob("Bloomberg.Download*.qfx"))
    if not qfx_paths:
        return

    for qfx_path in qfx_paths:
        _ingest_one_qfx(db_path, qfx_path, cusip_map)

    # Contributions: load + dedup + persist in one pass.
    _ingest_contributions_from_qfx(db_path, qfx_paths, cusip_map)


def _ingest_one_qfx(db_path: Path, qfx_path: Path, cusip_map: dict[str, str]) -> None:
    """Write a single QFX's snapshot + fund rows. Idempotent by snapshot_date."""
    snap = _parse_qfx(qfx_path, cusip_map)
    if not snap.funds:
        return
    conn = get_connection(db_path)
    try:
        snap_date = snap.date.isoformat()
        conn.execute("INSERT OR IGNORE INTO empower_snapshots (snapshot_date) VALUES (?)", (snap_date,))
        row = conn.execute("SELECT id FROM empower_snapshots WHERE snapshot_date = ?", (snap_date,)).fetchone()
        snapshot_id: int = row[0]
        conn.execute("DELETE FROM empower_funds WHERE snapshot_id = ?", (snapshot_id,))
        conn.executemany(
            "INSERT INTO empower_funds (snapshot_id, cusip, ticker, shares, price, mktval) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(snapshot_id, f.cusip, f.ticker, f.shares, f.price, f.mktval) for f in snap.funds],
        )
        conn.commit()
    finally:
        conn.close()


def _ingest_contributions_from_qfx(
    db_path: Path, qfx_paths: list[Path], cusip_map: dict[str, str],
) -> None:
    """Load BUYMF rows from every QFX, dedup across files, and persist."""
    all_contribs: list[Contribution] = []
    for qfx_path in qfx_paths:
        all_contribs.extend(_parse_qfx_contributions(qfx_path, cusip_map))
    if not all_contribs:
        return
    # Dedup by (date, amount, ticker) — overlapping QFX files may repeat rows.
    seen: set[tuple[date, float, str]] = set()
    unique: list[Contribution] = []
    for c in sorted(all_contribs, key=lambda x: x.date):
        key = (c.date, c.amount, c.ticker)
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)
    ingest_contributions(db_path, unique)


def ingest_contributions(db_path: Path, contribs: list[Contribution]) -> None:
    """Upsert contributions into ``empower_contributions``.

    Public so the build script can route Qianji-derived fallback contributions
    (for periods without a QFX file) through the same table that
    :func:`positions_at` reads. Deduplicates on the primary key
    ``(date, amount, ticker, cusip)``.
    """
    if not contribs:
        return
    conn = get_connection(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO empower_contributions (date, amount, ticker, cusip) "
            "VALUES (?, ?, ?, ?)",
            [(c.date.isoformat(), c.amount, c.ticker, c.cusip) for c in contribs],
        )
        conn.commit()
    finally:
        conn.close()


def positions_at(
    db_path: Path,
    as_of: date,
    prices: PriceContext,
    config: dict[str, object],
) -> list[PositionRow]:
    """Return one :class:`PositionRow` per 401k config ticker, scaled to ``as_of``.

    Algorithm mirrors the legacy :func:`etl.k401.daily_401k_values`:

    1. Find latest snapshot with ``snapshot_date <= as_of``. If none, return
       ``[]`` (before-first-snapshot is a real case during historical replay).
    2. For each fund in that snapshot, scale ``mktval`` by
       ``proxy_price(as_of) / proxy_price(snapshot_date)`` — falling back to
       the raw ``mktval`` if either proxy price is unavailable or the fund
       has no proxy mapping.
    3. Add any contributions with ``snapshot_date < contrib_date <= as_of``,
       each scaled by ``proxy_price(as_of) / proxy_price(contrib_date)``.

    :class:`PositionRow` for Empower never carries cost basis — QFX doesn't
    track it, so ``cost_basis_usd=None`` (spec invariant).
    """
    del config
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT id, snapshot_date FROM empower_snapshots "
            "WHERE snapshot_date <= ? ORDER BY snapshot_date DESC LIMIT 1",
            (as_of.isoformat(),),
        ).fetchone()
        if row is None:
            return []
        snapshot_id, snap_date_str = row
        snap_date = date.fromisoformat(snap_date_str)

        fund_rows = conn.execute(
            "SELECT ticker, shares, mktval FROM empower_funds WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()

        contrib_rows = conn.execute(
            "SELECT date, amount, ticker FROM empower_contributions "
            "WHERE date > ? AND date <= ? ORDER BY date",
            (snap_date_str, as_of.isoformat()),
        ).fetchall()
    finally:
        conn.close()

    # Pre-compute proxy price maps (per proxy, since the same proxy serves
    # multiple tickers). ``{}`` for missing proxies falls through to
    # raw-mktval behaviour in :func:`_ffill_proxy`.
    proxy_maps: dict[str, dict[date, float]] = {
        proxy: _proxy_prices_from_df(prices.prices, proxy)
        for proxy in set(PROXY_TICKERS.values())
    }

    # Aggregate by config ticker (a snapshot may have multiple funds with the
    # same config ticker — e.g., two S&P 500 variants).
    values_by_ticker: dict[str, float] = {}
    shares_by_ticker: dict[str, float] = {}
    for ticker, shares, mktval in fund_rows:
        proxy = PROXY_TICKERS.get(ticker)
        if proxy is None:
            # Unknown fund / unmapped ticker — use raw snapshot value.
            values_by_ticker[ticker] = values_by_ticker.get(ticker, 0.0) + float(mktval)
            shares_by_ticker[ticker] = shares_by_ticker.get(ticker, 0.0) + float(shares)
            continue
        pp = proxy_maps.get(proxy, {})
        snap_proxy = _ffill_proxy(pp, snap_date)
        curr_proxy = _ffill_proxy(pp, as_of)
        if snap_proxy and curr_proxy and snap_proxy > 0:
            scaled = float(mktval) * (curr_proxy / snap_proxy)
        else:
            scaled = float(mktval)
        values_by_ticker[ticker] = values_by_ticker.get(ticker, 0.0) + scaled
        shares_by_ticker[ticker] = shares_by_ticker.get(ticker, 0.0) + float(shares)

    # Add contributions (cumulative, each scaled from its own date).
    for c_date_str, amount, ticker in contrib_rows:
        c_date = date.fromisoformat(c_date_str)
        proxy = PROXY_TICKERS.get(ticker)
        if proxy is None:
            values_by_ticker[ticker] = values_by_ticker.get(ticker, 0.0) + float(amount)
            continue
        pp = proxy_maps.get(proxy, {})
        contrib_proxy = _ffill_proxy(pp, c_date)
        curr_proxy = _ffill_proxy(pp, as_of)
        if contrib_proxy and curr_proxy and contrib_proxy > 0:
            val = float(amount) * (curr_proxy / contrib_proxy)
        else:
            val = float(amount)
        values_by_ticker[ticker] = values_by_ticker.get(ticker, 0.0) + val

    return [
        PositionRow(
            ticker=ticker,
            value_usd=value,
            quantity=shares_by_ticker.get(ticker),
            cost_basis_usd=None,
            account=None,
        )
        for ticker, value in values_by_ticker.items()
    ]
