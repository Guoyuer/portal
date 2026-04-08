"""Build the timemachine SQLite database from raw data sources.

Integration script that:
  1. Initialises data/timemachine.db with all tables
  2. Ingests Fidelity brokerage transactions from CSV
  3. Ingests Empower 401k quarterly snapshots + contributions from QFX files
  4. Fetches and stores prices + CNY rates in timemachine.db.daily_close
  5. Computes daily allocation (reads prices from DB)
  6. Computes prefix sums
  7. Stores results

Usage:
  /c/Python314/python scripts/build_timemachine_db.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, date, datetime
from pathlib import Path

# Ensure the pipeline package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generate_asset_snapshot.allocation import compute_daily_allocation
from generate_asset_snapshot.db import (
    get_connection,
    ingest_empower_contributions,
    ingest_empower_qfx,
    ingest_fidelity_csv,
    init_db,
)
from generate_asset_snapshot.empower_401k import (
    PROXY_TICKERS,
    Contribution,
    daily_401k_values,
    load_all_contributions,
    load_all_qfx,
)
from generate_asset_snapshot.ingest.fidelity_history import load_transactions
from generate_asset_snapshot.ingest.qianji_db import load_all_from_db
from generate_asset_snapshot.precompute import build_daily_flows, compute_prefix_sums
from generate_asset_snapshot.prices import (
    fetch_and_store_cny_rates,
    fetch_and_store_prices,
    load_proxy_prices,
    symbol_holding_periods,
)
from generate_asset_snapshot.timemachine import DEFAULT_QJ_DB, _load_raw_rows, _parse_date

# ── Paths ────────────────────────────────────────────────────────────────────

PIPELINE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PIPELINE_DIR / "data"
DB_PATH = DATA_DIR / "timemachine.db"
FIDELITY_CSV = DATA_DIR / "fidelity_transactions.csv"
CONFIG_PATH = Path("C:/Users/guoyu/Projects/portal/data/config.json")
DOWNLOADS = Path("C:/Users/guoyu/Downloads")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _load_config(path: Path) -> dict[str, object]:
    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _f(val: object) -> float:
    """Cast object to float (safe for values known to be numeric)."""
    return float(val)  # type: ignore[arg-type]


def _load_401k_contributions(
    qfx_contribs: list[Contribution],
    last_qfx_date: date | None,
) -> list[Contribution]:
    """Merge 401k contributions: QFX BUYMF (primary) + Qianji (fallback after last QFX).

    QFX has per-fund CUSIP -> ticker, so each contribution knows its exact fund.
    Qianji only has total amount -- used for periods without QFX data (e.g. Q1 2026).
    For Qianji fallback, split 50/50 sp500/ex-us (current allocation).
    """
    contribs = list(qfx_contribs)

    # Add Qianji contributions AFTER the last QFX coverage
    if last_qfx_date and DEFAULT_QJ_DB.exists():
        conn = sqlite3.connect(f"file:{DEFAULT_QJ_DB}?mode=ro", uri=True)
        for money, ts in conn.execute(
            "SELECT money, time FROM user_bill WHERE status = 1 AND type = 1 AND fromact = '401k' ORDER BY time"
        ):
            d = datetime.fromtimestamp(ts, tz=UTC).date()
            if d > last_qfx_date:
                amt = float(money)
                contribs.append(Contribution(date=d, amount=amt * 0.5, ticker="401k sp500"))
                contribs.append(Contribution(date=d, amount=amt * 0.5, ticker="401k ex-us"))
        conn.close()

    contribs.sort(key=lambda c: c.date)
    return contribs


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    print("=" * 60)
    print("  Timemachine DB Builder")
    print("=" * 60)

    # ── Step 1: Initialise database ──────────────────────────────────────────
    print("\n[1/7] Initialising database...")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    init_db(DB_PATH)
    print(f"  Database ready: {DB_PATH}")

    # ── Step 2: Ingest Fidelity transactions ─────────────────────────────────
    print("\n[2/7] Ingesting Fidelity transactions...")
    if not FIDELITY_CSV.exists():
        print(f"  ERROR: CSV not found at {FIDELITY_CSV}")
        sys.exit(1)
    count = ingest_fidelity_csv(DB_PATH, FIDELITY_CSV)
    print(f"  {count} rows in fidelity_transactions table")

    # ── Step 3: Ingest Empower QFX files + contributions ─────────────────────
    print("\n[3/7] Ingesting Empower 401k QFX files...")
    qfx_files = sorted(DOWNLOADS.glob("Bloomberg.Download*.qfx"))
    if not qfx_files:
        print("  WARNING: No QFX files found in Downloads")
    else:
        total_funds = 0
        for qfx_path in qfx_files:
            n = ingest_empower_qfx(DB_PATH, qfx_path)
            total_funds += n
        print(f"  Ingested {len(qfx_files)} QFX files ({total_funds} fund positions)")

    # Ingest QFX contributions into DB
    qfx_contribs = load_all_contributions(DOWNLOADS)
    if qfx_contribs:
        contrib_count = ingest_empower_contributions(DB_PATH, qfx_contribs)
        print(f"  {contrib_count} QFX contributions in empower_contributions table")

    # ── Step 4: Fetch & store prices + CNY rates ─────────────────────────────
    print("\n[4/7] Fetching prices...")

    config = _load_config(CONFIG_PATH)
    print("  Config loaded")

    # Determine date range from Fidelity transactions
    rows = _load_raw_rows(FIDELITY_CSV)
    start = min(_parse_date(r["Run Date"]) for r in rows)
    end = date.today()
    print(f"  Date range: {start} -> {end}")

    # Symbol holding periods
    periods = symbol_holding_periods(FIDELITY_CSV)
    print(f"  {len(periods)} symbols with holding periods")

    # 401k snapshots + proxy tickers
    qfx_snaps = load_all_qfx(DOWNLOADS)
    if qfx_snaps:
        print(f"  {len(qfx_snaps)} 401k quarterly snapshots ({qfx_snaps[0].date} -> {qfx_snaps[-1].date})")
    else:
        print("  No 401k QFX snapshots found")

    # Ensure proxy tickers cover full 401k range (earliest QFX -> today)
    proxy_start = qfx_snaps[0].date if qfx_snaps else start
    for proxy in PROXY_TICKERS.values():
        existing = periods.get(proxy)
        if existing is None or existing[0] > proxy_start:
            periods[proxy] = (proxy_start, None)

    # Fetch and store in timemachine.db (NOT prices.db)
    fetch_and_store_prices(DB_PATH, periods, end)
    fetch_and_store_cny_rates(DB_PATH, start, end)

    # ── Step 5: Compute allocation (reads prices from DB) ────────────────────
    print("\n[5/7] Computing allocation...")

    # 401k contributions: QFX BUYMF (primary) + Qianji fallback
    proxy_prices = load_proxy_prices(DB_PATH, PROXY_TICKERS)
    last_qfx_date = qfx_snaps[-1].date if qfx_snaps else None
    k401_contribs = _load_401k_contributions(qfx_contribs, last_qfx_date)
    qfx_only = sum(1 for c in k401_contribs if c.date <= (last_qfx_date or date.min))
    qj_only = len(k401_contribs) - qfx_only
    if k401_contribs:
        print(f"  {len(k401_contribs)} 401k contributions ({qfx_only} from QFX, {qj_only} from Qianji fallback)")
    k401_daily = daily_401k_values(qfx_snaps, proxy_prices, start, end, contributions=k401_contribs)
    print(f"  401k daily values: {len(k401_daily)} days")

    # Compute daily allocation (reads prices + CNY from DB internally)
    print("  Computing daily allocation (this may take a minute)...")
    alloc = compute_daily_allocation(DB_PATH, DEFAULT_QJ_DB, config, k401_daily, start, end)
    print(f"  {len(alloc)} daily records computed")

    # ── Step 6: Store in computed_daily + computed_daily_tickers ──────────────
    print("\n[6/7] Writing computed_daily + computed_daily_tickers tables...")
    conn = get_connection(DB_PATH)
    try:
        conn.execute("DELETE FROM computed_daily")
        conn.execute("DELETE FROM computed_daily_tickers")
        ticker_count = 0
        for r in alloc:
            total = _f(r["total"])
            conn.execute(
                "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net, liabilities)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (r["date"], total, _f(r["us_equity"]), _f(r["non_us_equity"]),
                 _f(r["crypto"]), _f(r["safe_net"]), _f(r.get("liabilities", 0))),
            )
            for t in r.get("tickers", []):
                conn.execute(
                    "INSERT OR REPLACE INTO computed_daily_tickers"
                    " (date, ticker, value, category, subtype, cost_basis, gain_loss, gain_loss_pct)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (r["date"], t["ticker"], t["value"], t["category"], t["subtype"],
                     t["cost_basis"], t["gain_loss"], t["gain_loss_pct"]),
                )
                ticker_count += 1
        conn.commit()
        row_count: int = conn.execute("SELECT COUNT(*) FROM computed_daily").fetchone()[0]
    finally:
        conn.close()
    print(f"  {row_count} rows in computed_daily, {ticker_count} rows in computed_daily_tickers")

    # ── Step 7: Compute prefix sums from transactions ───────────────────────
    print("\n[7/7] Computing prefix sums from transactions...")
    fidelity_txns = load_transactions(FIDELITY_CSV)
    qianji_records, _ = load_all_from_db(DEFAULT_QJ_DB)
    daily_flows = build_daily_flows(
        fidelity_txns, qianji_records, start.isoformat(), end.isoformat(),  # type: ignore[arg-type]
    )
    prefix_rows = compute_prefix_sums(daily_flows)
    print(f"  {len(daily_flows)} days with transactions -> {len(prefix_rows)} prefix rows")

    # Forward-fill prefix to match all daily dates
    daily_dates = sorted(str(r["date"]) for r in alloc)
    prefix_by_date: dict[str, dict[str, object]] = {str(r["date"]): r for r in prefix_rows}
    prefix_fields = ["income", "expenses", "buys", "sells", "dividends", "netCashIn", "ccPayments"]
    last_prefix: dict[str, float] = {f: 0.0 for f in prefix_fields}
    aligned_prefix: list[dict[str, object]] = []
    for d in daily_dates:
        if d in prefix_by_date:
            last_prefix = {f: _f(prefix_by_date[d].get(f, 0)) for f in prefix_fields}
        aligned_prefix.append({"date": d, **last_prefix})
    print(f"  Forward-filled to {len(aligned_prefix)} rows (aligned with daily)")

    conn = get_connection(DB_PATH)
    try:
        conn.execute("DELETE FROM computed_prefix")
        conn.executemany(
            "INSERT INTO computed_prefix (date, income, expenses, buys, sells, dividends, net_cash_in, cc_payments)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    r["date"],
                    r.get("income", 0),
                    r.get("expenses", 0),
                    r.get("buys", 0),
                    r.get("sells", 0),
                    r.get("dividends", 0),
                    r.get("netCashIn", 0),
                    r.get("ccPayments", 0),
                )
                for r in aligned_prefix
            ],
        )
        conn.commit()
        prefix_count: int = conn.execute("SELECT COUNT(*) FROM computed_prefix").fetchone()[0]
    finally:
        conn.close()
    print(f"  {prefix_count} rows in computed_prefix table")

    # ── Verify daily_close populated ─────────────────────────────────────────
    conn = get_connection(DB_PATH)
    try:
        price_count: int = conn.execute("SELECT COUNT(*) FROM daily_close").fetchone()[0]
    finally:
        conn.close()
    print(f"\n  daily_close: {price_count} rows (prices + CNY rates)")

    # ── Summary ──────────────────────────────────────────────────────────────
    if alloc:
        latest = alloc[-1]
        earliest = alloc[0]
        print("\n" + "=" * 60)
        print("  Build complete!")
        print(f"  Earliest: {earliest['date']}  total=${_f(earliest['total']):,.0f}")
        print(f"  Latest:   {latest['date']}  total=${_f(latest['total']):,.0f}")
        def _safe_pct(r: dict[str, object]) -> float:
            total = _f(r["total"])
            return round(_f(r["safe_net"]) / total * 100, 1) if total > 0 else 0
        print(f"  Safe Net %: {min(_safe_pct(r) for r in alloc):.1f}% -- {max(_safe_pct(r) for r in alloc):.1f}%")
        print("=" * 60)

    print("\nTo start the server:")
    print("  python -m generate_asset_snapshot.server")


if __name__ == "__main__":
    main()
