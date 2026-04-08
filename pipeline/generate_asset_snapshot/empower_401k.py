"""Parse Empower 401k QFX files and compute daily values via proxy tickers.

Each QFX file contains a quarterly snapshot with exact market values per fund.
Between snapshots, daily values are interpolated using proxy ticker returns:
  value(date) = snapshot_mktval × (proxy_close(date) / proxy_close(snapshot_date))

Fund → config ticker → proxy mapping:
  S&P 500 variants  → "401k sp500" → VOO
  Harbor Capital     → "401k tech"  → QQQM
  ex-US variants     → "401k ex-us" → VXUS
  LifePath 2060      → "401k sp500" → VOO (approximation)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

# ── CUSIP → config ticker → proxy ticker ──────────────────────────────────────

CUSIP_MAP: dict[str, str] = {
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


# ── QFX parsing ───────────────────────────────────────────────────────────────

@dataclass
class FundSnapshot:
    """Single fund position at a point in time."""
    date: date
    cusip: str
    ticker: str       # config ticker (e.g. "401k sp500")
    shares: float
    price: float
    mktval: float


@dataclass
class QuarterSnapshot:
    """All fund positions at quarter end."""
    date: date
    funds: list[FundSnapshot]
    total: float      # INV401KBAL


def _extract_tag(text: str, tag: str) -> str:
    """Extract value between <TAG>value</TAG> or <TAG>value (OFX shorthand)."""
    m = re.search(rf"<{tag}>\s*([^<\s]+)", text)
    return m.group(1).strip() if m else ""


def parse_qfx(path: Path) -> QuarterSnapshot:
    """Parse a single Empower QFX file into a QuarterSnapshot."""
    text = path.read_text(encoding="ascii", errors="replace")

    # Date range
    dt_end = _extract_tag(text, "DTEND")
    snap_date = datetime.strptime(dt_end[:8], "%Y%m%d").date()

    # Position list (POSMF blocks — OFX/SGML, no closing tags)
    funds: list[FundSnapshot] = []
    for m in re.finditer(r"<POSMF>(.*?)(?=<POSMF>|</INVPOSLIST>|$)", text, re.DOTALL):
        block = m.group(1)
        cusip = _extract_tag(block, "UNIQUEID")
        units = float(_extract_tag(block, "UNITS") or "0")
        price = float(_extract_tag(block, "UNITPRICE") or "0")
        mktval = float(_extract_tag(block, "MKTVAL") or "0")
        if mktval <= 0 or units <= 0:
            continue
        ticker = CUSIP_MAP.get(cusip, f"401k_unknown_{cusip}")
        funds.append(FundSnapshot(
            date=snap_date, cusip=cusip, ticker=ticker,
            shares=units, price=price, mktval=mktval,
        ))

    # Total = sum of fund market values (INV401KBAL/TOTAL is unreliable)
    total = sum(f.mktval for f in funds)

    return QuarterSnapshot(date=snap_date, funds=funds, total=total)


def parse_qfx_contributions(path: Path) -> list[Contribution]:
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
        ticker = CUSIP_MAP.get(cusip, "401k sp500")
        contribs.append(Contribution(date=d, amount=amount, ticker=ticker))
    return contribs


def load_all_qfx(directory: Path, glob: str = "Bloomberg.Download*.qfx") -> list[QuarterSnapshot]:
    """Load and sort all QFX files from a directory."""
    snapshots = []
    for path in directory.glob(glob):
        snap = parse_qfx(path)
        if snap.funds:
            snapshots.append(snap)
    snapshots.sort(key=lambda s: s.date)
    # Deduplicate same-date snapshots (keep last loaded)
    seen: dict[date, QuarterSnapshot] = {}
    for s in snapshots:
        seen[s.date] = s
    return sorted(seen.values(), key=lambda s: s.date)


def load_all_contributions(directory: Path, glob: str = "Bloomberg.Download*.qfx") -> list[Contribution]:
    """Load and deduplicate all BUYMF contributions from QFX files."""
    all_contribs: list[Contribution] = []
    for path in directory.glob(glob):
        all_contribs.extend(parse_qfx_contributions(path))
    # Dedup by (date, amount, ticker) — overlapping QFX files may repeat transactions
    seen: set[tuple[date, float, str]] = set()
    unique: list[Contribution] = []
    for c in sorted(all_contribs, key=lambda x: x.date):
        key = (c.date, c.amount, c.ticker)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


# ── Daily value interpolation ─────────────────────────────────────────────────

@dataclass
class Contribution:
    """A 401k contribution (BUYMF from QFX)."""
    date: date
    amount: float
    ticker: str  # config ticker, e.g. "401k sp500"


def _ffill_proxy(prices: dict[date, float], d: date) -> float | None:
    """Find most recent proxy price on or before d (up to 7 days back)."""
    from datetime import timedelta
    for i in range(8):
        p = prices.get(d - timedelta(days=i))
        if p is not None:
            return p
    return None


def daily_401k_values(
    snapshots: list[QuarterSnapshot],
    proxy_prices: dict[str, dict[date, float]],
    start: date,
    end: date,
    contributions: list[Contribution] | None = None,
) -> dict[date, dict[str, float]]:
    """Compute daily 401k values by config ticker.

    Args:
        snapshots: Sorted quarterly snapshots from QFX files.
        proxy_prices: {proxy_ticker: {date: close_price}} for VOO, QQQM, VXUS.
        start: First date to compute.
        end: Last date to compute.
        contributions: Post-snapshot 401k contributions from Qianji.
            Each contribution is split across funds in proportion to the
            last snapshot's allocation, then scaled by proxy returns.

    Returns:
        {date: {"401k sp500": value, "401k tech": value, "401k ex-us": value}}
    """
    if not snapshots:
        return {}

    from datetime import timedelta

    all_contribs = sorted(contributions or [], key=lambda c: c.date)

    result: dict[date, dict[str, float]] = {}
    current = start

    while current <= end:
        # Find the most recent snapshot on or before current date
        snap = None
        for s in snapshots:
            if s.date <= current:
                snap = s
            else:
                break

        if snap is None:
            current += timedelta(days=1)
            continue

        day_values: dict[str, float] = {}

        # Base: snapshot funds scaled by proxy returns
        for fund in snap.funds:
            proxy = PROXY_TICKERS.get(fund.ticker)
            if not proxy:
                day_values[fund.ticker] = day_values.get(fund.ticker, 0) + fund.mktval
                continue

            pp = proxy_prices.get(proxy, {})
            snap_proxy = _ffill_proxy(pp, snap.date)
            curr_proxy = _ffill_proxy(pp, current)

            if snap_proxy and curr_proxy and snap_proxy > 0:
                scaled = fund.mktval * (curr_proxy / snap_proxy)
            else:
                scaled = fund.mktval

            day_values[fund.ticker] = day_values.get(fund.ticker, 0) + scaled

        # Add contributions made AFTER this snapshot up to current date.
        # Each contribution knows its own ticker (from QFX BUYMF CUSIP),
        # scaled by proxy returns from contribution date to current date.
        for contrib in all_contribs:
            if contrib.date <= snap.date or contrib.date > current:
                continue
            proxy = PROXY_TICKERS.get(contrib.ticker)
            if not proxy:
                day_values[contrib.ticker] = day_values.get(contrib.ticker, 0) + contrib.amount
                continue
            pp = proxy_prices.get(proxy, {})
            contrib_proxy = _ffill_proxy(pp, contrib.date)
            curr_proxy = _ffill_proxy(pp, current)
            if contrib_proxy and curr_proxy and contrib_proxy > 0:
                val = contrib.amount * (curr_proxy / contrib_proxy)
            else:
                val = contrib.amount
            day_values[contrib.ticker] = day_values.get(contrib.ticker, 0) + val

        if day_values:
            result[current] = day_values

        current += timedelta(days=1)

    return result
