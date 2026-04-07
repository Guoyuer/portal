"""Build the timemachine SQLite database from raw data sources.

Integration script that:
  1. Initialises data/timemachine.db with all tables
  2. Ingests Fidelity brokerage transactions from CSV
  3. Ingests Empower 401k quarterly snapshots from QFX files
  4. Runs the verified allocation pipeline (safe_net_history.py) to compute
     daily portfolio values per asset category
  5. Stores results in the computed_daily table

Usage:
  /c/Python314/python scripts/build_timemachine_db.py
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, date, datetime
from pathlib import Path

# Ensure the pipeline package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generate_asset_snapshot.db import get_connection, ingest_empower_qfx, ingest_fidelity_csv, init_db
from generate_asset_snapshot.empower_401k import PROXY_TICKERS, Contribution, daily_401k_values, load_all_qfx
from generate_asset_snapshot.timemachine import DEFAULT_QJ_DB, _load_raw_rows, _parse_date
from scripts.safe_net_history import (
    PRICE_DB,
    _init_price_db,
    compute_daily_allocation,
    fetch_cny_rates,
    fetch_prices,
    load_config,
    symbol_holding_periods,
)

# ── Paths ────────────────────────────────────────────────────────────────────

PIPELINE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PIPELINE_DIR / "data"
DB_PATH = DATA_DIR / "timemachine.db"
FIDELITY_CSV = DATA_DIR / "fidelity_transactions.csv"
CONFIG_PATH = Path("C:/Users/guoyu/Projects/portal/data/config.json")
DOWNLOADS = Path("C:/Users/guoyu/Downloads")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_401k_contributions() -> list[Contribution]:
    """Load 401k contributions from Qianji database."""
    if not DEFAULT_QJ_DB.exists():
        print("  Qianji DB not found — skipping 401k contributions")
        return []
    conn = sqlite3.connect(f"file:{DEFAULT_QJ_DB}?mode=ro", uri=True)
    contribs: list[Contribution] = []
    for money, ts in conn.execute(
        "SELECT money, time FROM user_bill WHERE status = 1 AND type = 1 AND fromact = '401k' ORDER BY time"
    ):
        d = datetime.fromtimestamp(ts, tz=UTC).date()
        contribs.append(Contribution(date=d, amount=float(money)))
    conn.close()
    return contribs


def _build_proxy_prices() -> dict[str, dict[date, float]]:
    """Load proxy ticker prices from the prices.db cache."""
    proxy_prices: dict[str, dict[date, float]] = {}
    conn = _init_price_db(PRICE_DB)
    for proxy in PROXY_TICKERS.values():
        proxy_prices[proxy] = {}
        for d, close in conn.execute(
            "SELECT date, close FROM daily_close WHERE symbol = ? ORDER BY date",
            (proxy,),
        ):
            proxy_prices[proxy][date.fromisoformat(d)] = close
    conn.close()
    return proxy_prices


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  Timemachine DB Builder")
    print("=" * 60)

    # ── Step 1: Initialise database ──────────────────────────────────────────
    print("\n[1/5] Initialising database...")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    init_db(DB_PATH)
    print(f"  Database ready: {DB_PATH}")

    # ── Step 2: Ingest Fidelity transactions ─────────────────────────────────
    print("\n[2/5] Ingesting Fidelity transactions...")
    if not FIDELITY_CSV.exists():
        print(f"  ERROR: CSV not found at {FIDELITY_CSV}")
        sys.exit(1)
    count = ingest_fidelity_csv(DB_PATH, FIDELITY_CSV)
    print(f"  {count} rows in fidelity_transactions table")

    # ── Step 3: Ingest Empower QFX files ─────────────────────────────────────
    print("\n[3/5] Ingesting Empower 401k QFX files...")
    qfx_files = sorted(DOWNLOADS.glob("Bloomberg.Download*.qfx"))
    if not qfx_files:
        print("  WARNING: No QFX files found in Downloads")
    else:
        total_funds = 0
        for qfx_path in qfx_files:
            n = ingest_empower_qfx(DB_PATH, qfx_path)
            total_funds += n
        print(f"  Ingested {len(qfx_files)} QFX files ({total_funds} fund positions)")

    # ── Step 4: Fetch prices + compute allocation ────────────────────────────
    print("\n[4/5] Running allocation pipeline...")

    config = load_config(CONFIG_PATH)
    print("  Config loaded")

    # Determine date range from Fidelity transactions
    rows = _load_raw_rows(FIDELITY_CSV)
    start = min(_parse_date(r["Run Date"]) for r in rows)
    end = date.today()
    print(f"  Date range: {start} → {end}")

    # Symbol holding periods
    periods = symbol_holding_periods(FIDELITY_CSV)
    print(f"  {len(periods)} symbols with holding periods")

    # 401k snapshots + proxy tickers
    qfx_snaps = load_all_qfx(DOWNLOADS)
    if qfx_snaps:
        print(f"  {len(qfx_snaps)} 401k quarterly snapshots ({qfx_snaps[0].date} → {qfx_snaps[-1].date})")
    else:
        print("  No 401k QFX snapshots found")

    # Ensure proxy tickers get prices fetched
    for proxy in PROXY_TICKERS.values():
        if proxy not in periods:
            periods[proxy] = (start, None)

    # Fetch historical prices + CNY rates
    prices = fetch_prices(periods, end)
    cny_rates = fetch_cny_rates(start, end)

    # 401k daily values via proxy interpolation
    proxy_prices = _build_proxy_prices()
    k401_contribs = _load_401k_contributions()
    if k401_contribs:
        print(f"  {len(k401_contribs)} 401k contributions from Qianji")
    k401_daily = daily_401k_values(qfx_snaps, proxy_prices, start, end, contributions=k401_contribs)
    print(f"  401k daily values: {len(k401_daily)} days")

    # Compute daily allocation
    print("  Computing daily allocation (this may take a minute)...")
    alloc = compute_daily_allocation(FIDELITY_CSV, DEFAULT_QJ_DB, config, prices, cny_rates, k401_daily, start, end)
    print(f"  {len(alloc)} daily records computed")

    # ── Step 5: Store in computed_daily table ────────────────────────────────
    print("\n[5/5] Writing computed_daily table...")
    conn = get_connection(DB_PATH)
    try:
        conn.execute("DELETE FROM computed_daily")
        conn.executemany(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    r["date"],
                    r["total"],
                    round(r["total"] * r["us_equity_pct"] / 100, 2) if r["total"] else 0,
                    round(r["total"] * r["non_us_equity_pct"] / 100, 2) if r["total"] else 0,
                    round(r["total"] * r["crypto_pct"] / 100, 2) if r["total"] else 0,
                    r["safe_net"],
                )
                for r in alloc
            ],
        )
        conn.commit()
        row_count: int = conn.execute("SELECT COUNT(*) FROM computed_daily").fetchone()[0]
    finally:
        conn.close()
    print(f"  {row_count} rows in computed_daily table")

    # ── Summary ──────────────────────────────────────────────────────────────
    if alloc:
        latest = alloc[-1]
        earliest = alloc[0]
        print("\n" + "=" * 60)
        print("  Build complete!")
        print(f"  Earliest: {earliest['date']}  total=${earliest['total']:,.0f}")
        print(f"  Latest:   {latest['date']}  total=${latest['total']:,.0f}")
        print(f"  Safe Net %: {min(r['safe_net_pct'] for r in alloc):.1f}% — {max(r['safe_net_pct'] for r in alloc):.1f}%")
        print("=" * 60)

    print("\nTo start the server:")
    print("  python -m generate_asset_snapshot.server")


if __name__ == "__main__":
    main()
