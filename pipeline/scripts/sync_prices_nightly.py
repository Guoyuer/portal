"""Nightly closing-price sync for GitHub Actions.

Stateless price-only incremental: pull each symbol's ``MAX(date)`` from D1,
fetch the gap (to today) via yfinance, push only the new rows back to D1 as
``INSERT OR IGNORE``. No local ``timemachine.db`` is built — we only maintain
symbols already present in the cache. Initial seeding of new symbols still
happens through the local pipeline's ``sync_to_d1.py`` path.

Requires ``CLOUDFLARE_API_TOKEN`` + ``CLOUDFLARE_ACCOUNT_ID`` in the environment
(wrangler reads them directly) and Node/npm on PATH for ``npx wrangler``.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import yfinance as yf

_PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_DIR))

from etl.market._yfinance import extract_close  # noqa: E402
from etl.prices import (  # noqa: E402
    _build_split_factors,
    _holding_periods_from_action_kind_rows,
    _reverse_split_factor,
)
from etl.refresh import refresh_window_start  # noqa: E402
from etl.sources import ActionKind  # noqa: E402
from scripts._wrangler import (  # noqa: E402
    run_wrangler_exec_file,
    run_wrangler_query,
    sql_escape,
)

# Stop fetching prices this long after a position was fully closed.
_CLOSED_POSITION_GRACE_DAYS = 7

# ``action_kind`` values whose rows the holding-period accumulator consumes.
# Pulled at query time so D1 only sends rows the accumulator will actually
# look at.
_POSITION_KIND_VALUES = tuple(k.value for k in (
    ActionKind.BUY, ActionKind.SELL, ActionKind.REINVESTMENT,
    ActionKind.REDEMPTION, ActionKind.DISTRIBUTION, ActionKind.EXCHANGE,
    ActionKind.TRANSFER,
))


# ── D1 state loaders ────────────────────────────────────────────────────────


def _load_holdings_from_d1() -> dict[str, tuple[date, date | None]]:
    """Reconstruct ``{symbol: (first_buy, last_sell_or_None)}`` from D1."""
    placeholders = ", ".join(f"'{v}'" for v in _POSITION_KIND_VALUES)
    rows = run_wrangler_query(
        "SELECT run_date, action_kind, symbol, quantity"
        f" FROM fidelity_transactions WHERE action_kind IN ({placeholders})"
        " ORDER BY run_date, id"
    )
    tuples = [
        (
            r.get("run_date") or "",
            (r.get("symbol") or "").strip(),
            (r.get("action_kind") or "").strip(),
            float(r.get("quantity") or 0),
        )
        for r in rows
    ]
    return _holding_periods_from_action_kind_rows(tuples)


def _load_cached_max_from_d1() -> dict[str, date]:
    rows = run_wrangler_query(
        "SELECT symbol, MAX(date) AS max_date FROM daily_close GROUP BY symbol"
    )
    out: dict[str, date] = {}
    for r in rows:
        md = r.get("max_date")
        if md:
            out[r["symbol"]] = date.fromisoformat(md)
    return out


# ── yfinance fetchers ───────────────────────────────────────────────────────


def _fetch_equity_rows(
    equity_syms: set[str],
    holdings: dict[str, tuple[date, date | None]],
    cached_max: dict[str, date],
    today: date,
) -> list[tuple[str, str, float]]:
    """Fetch (cached_max + 1, rolled back by refresh window) → today for each equity symbol."""
    # Reach back into the refresh window so Yahoo late corrections land even
    # when cached_max already covers today. The min() below also preserves the
    # historical-gap case: if cached_max is further back than the tail (rare
    # after a long outage), we still fetch from cached_max + 1 forward.
    refresh_floor = refresh_window_start(today)
    ranges: dict[str, tuple[date, date]] = {}
    for sym in equity_syms:
        period = holdings.get(sym)
        if period:
            hp_start, hp_end = period
            # Stop fetching long-closed positions
            if hp_end and hp_end < today - timedelta(days=_CLOSED_POSITION_GRACE_DAYS):
                continue
            need_end = min(hp_end, today) if hp_end else today
        else:
            # Proxies (VOO/QQQM/VXUS), market indices, etc. — track to today.
            need_end = today
        start = min(cached_max[sym] + timedelta(days=1), refresh_floor)
        if start > need_end:
            continue
        ranges[sym] = (start, need_end)

    if not ranges:
        print("  equity: nothing to fetch (all symbols up to date)")
        return []

    syms = sorted(ranges)
    batch_start = min(s for s, _ in ranges.values())
    batch_end = max(e for _, e in ranges.values())
    print(f"  equity: {len(syms)} symbols, {batch_start} → {batch_end}")

    df = yf.download(
        " ".join(syms),
        start=batch_start.isoformat(),
        end=(batch_end + timedelta(days=1)).isoformat(),
        auto_adjust=False,
        progress=False,
    )
    if df.empty:
        print("  equity: yfinance returned empty DataFrame (holiday / too early?)")
        return []

    close_df = extract_close(df, syms)

    splits = _build_split_factors(syms)
    out: list[tuple[str, str, float]] = []
    for sym in close_df.columns:
        start, end = ranges[sym]
        factors = splits.get(sym, [])
        for dt, price in close_df[sym].dropna().items():
            d = dt.date() if hasattr(dt, "date") else dt
            if d < start or d > end:
                continue
            unadj = float(price) * _reverse_split_factor(d, factors)
            out.append((sym, d.isoformat(), unadj))
    return out


def _fetch_cny_rows(today: date, cached: date | None) -> list[tuple[str, str, float]]:
    if cached is None:
        print("  CNY=X: no prior cache, skipping (nightly script only fills gaps)")
        return []
    start = cached + timedelta(days=1)
    if start > today:
        print("  CNY=X: up to date")
        return []
    print(f"  CNY=X: {start} → {today}")
    df = yf.download(
        "CNY=X",
        start=start.isoformat(),
        end=(today + timedelta(days=1)).isoformat(),
        auto_adjust=False,
        progress=False,
    )
    if df.empty:
        print("  CNY=X: yfinance empty")
        return []
    close_df = extract_close(df, ["CNY=X"])
    if close_df.empty:
        print("  CNY=X: extract_close found no Close data")
        return []
    close = close_df.iloc[:, 0]
    out: list[tuple[str, str, float]] = []
    for dt, rate in close.dropna().items():
        d = dt.date() if hasattr(dt, "date") else dt
        if d < start or d > today:
            continue
        out.append(("CNY=X", d.isoformat(), float(rate)))
    return out


# ── main ────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Nightly closing-price sync to D1")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and print SQL without executing against D1",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    today = date.today()

    print("Step 1: pulling state from D1...")
    cached_max = _load_cached_max_from_d1()
    print(f"  cached symbols: {len(cached_max)}")
    if not cached_max:
        print("D1 daily_close is empty — seed via local sync first. Nothing to do.")
        return

    holdings = _load_holdings_from_d1()
    print(f"  holdings: {len(holdings)}")

    print("Step 2: fetching gaps from yfinance...")
    equity_syms = {s for s in cached_max if s != "CNY=X"}
    equity_rows = _fetch_equity_rows(equity_syms, holdings, cached_max, today)
    cny_rows = _fetch_cny_rows(today, cached_max.get("CNY=X"))
    all_rows = equity_rows + cny_rows
    print(f"  new rows: {len(all_rows)} ({len(equity_rows)} equity + {len(cny_rows)} CNY)")

    if not all_rows:
        print("Nothing to push.")
        return

    sql_lines = [
        "INSERT OR IGNORE INTO daily_close (symbol, date, close) VALUES"
        f" ({sql_escape(s)}, {sql_escape(d)}, {sql_escape(c)});"
        for s, d, c in all_rows
    ]
    sql_text = "\n".join(sql_lines) + "\n"

    if args.dry_run:
        print(f"\n[dry-run] {len(sql_lines)} statements, {len(sql_text):,} bytes")
        print("[dry-run] preview:\n" + "\n".join(sql_lines[:10]))
        return

    print(f"Step 3: pushing {len(all_rows)} rows to D1...")
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sql", delete=False, encoding="utf-8"
    ) as f:
        f.write(sql_text)
        sql_path = Path(f.name)
    try:
        run_wrangler_exec_file(sql_path)
    finally:
        sql_path.unlink(missing_ok=True)
    print("Done.")


if __name__ == "__main__":
    main()
