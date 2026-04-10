"""Build the timemachine SQLite database from raw data sources.

Integration script that:
  1. Initialises data/timemachine.db with all tables
  2. Ingests Fidelity brokerage transactions from CSV
  3. Ingests Empower 401k quarterly snapshots + contributions from QFX files
  4. Fetches and stores prices + CNY rates in timemachine.db.daily_close
  5. Computes daily allocation (reads prices from DB)
  6. Stores results

Modes:
  (default)       Full rebuild — recompute everything, overwrite DB
  --incremental   Only compute dates after last persisted date
  --verify        Full recompute + diff against persisted data (no writes)

Usage:
  /c/Python314/python scripts/build_timemachine_db.py [--incremental | --verify]
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys
from datetime import UTC, date, datetime, timedelta
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
from generate_asset_snapshot.incremental import append_daily, get_last_computed_date, verify_daily
from generate_asset_snapshot.ingest.qianji_db import load_all_from_db
from generate_asset_snapshot.precompute import (
    precompute_holdings_detail,
    precompute_market,
)
from generate_asset_snapshot.prices import (
    fetch_and_store_cny_rates,
    fetch_and_store_prices,
    load_proxy_prices,
    symbol_holding_periods,
)
from generate_asset_snapshot.timemachine import DEFAULT_QJ_DB, _load_raw_rows, _parse_date
from generate_asset_snapshot.validate import Severity, validate_build

# ── Paths ────────────────────────────────────────────────────────────────────

PIPELINE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PIPELINE_DIR / "data"
DB_PATH = DATA_DIR / "timemachine.db"
FIDELITY_CSV = DATA_DIR / "fidelity_transactions.csv"
CONFIG_PATH = Path(os.environ.get("PORTAL_CONFIG", PIPELINE_DIR.parent / "data" / "config.json"))
DOWNLOADS = Path(os.environ.get("PORTAL_DOWNLOADS", Path.home() / "Downloads"))
ROBINHOOD_CSV = DOWNLOADS / "Robinhood_history.csv"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _load_config(path: Path) -> dict[str, object]:
    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _f(val: object) -> float:
    """Cast object to float (safe for values known to be numeric)."""
    return float(val)  # type: ignore[arg-type]


def _merge_fidelity_csvs() -> Path:
    """Merge all Fidelity CSVs from Downloads into a single file.

    Scans ~/Downloads for Accounts_History*.csv, deduplicates rows,
    and writes the merged result to pipeline/data/fidelity_transactions.csv.
    If --csv <path> is given, uses that single file instead.
    If no Downloads CSVs found, falls back to existing merged file.
    """
    # Check for --csv <path> argument
    if "--csv" in sys.argv:
        idx = sys.argv.index("--csv")
        if idx + 1 < len(sys.argv):
            p = Path(sys.argv[idx + 1])
            if not p.exists():
                print(f"  ERROR: --csv file not found: {p}")
                sys.exit(1)
            print(f"  Using single CSV: {p}")
            return p

    # Scan Downloads for Accounts_History*.csv
    raw_csvs = sorted(DOWNLOADS.glob("Accounts_History*.csv"))
    if not raw_csvs:
        if FIDELITY_CSV.exists():
            print(f"  No Accounts_History CSVs in Downloads, using existing {FIDELITY_CSV.name}")
            return FIDELITY_CSV
        print("  ERROR: No Fidelity CSVs found in Downloads or pipeline/data/")
        sys.exit(1)

    print(f"  Found {len(raw_csvs)} CSVs in Downloads, merging...")

    # Canonical output columns (superset of all formats)
    out_cols = [
        "Run Date", "Account", "Account Number", "Action", "Symbol", "Description",
        "Type", "Exchange Quantity", "Exchange Currency", "Quantity", "Currency",
        "Price", "Exchange Rate", "Commission", "Fees", "Accrued Interest",
        "Amount", "Settlement Date",
    ]

    # Read all CSVs with DictReader, normalise to canonical columns.
    # Dedup across files (overlapping quarterly exports) but NOT within the same file —
    # Fidelity can have multiple identical transactions on the same day (same symbol,
    # qty, amount) that are legitimately distinct trades.
    cross_file_seen: dict[tuple[str, ...], str] = {}  # key -> source filename
    parsed: list[dict[str, str]] = []

    for csv_path in raw_csvs:
        text = csv_path.read_text(encoding="utf-8-sig")
        lines = text.splitlines()
        hdr_idx = -1
        for i, line in enumerate(lines):
            if line.strip().startswith("Run Date"):
                hdr_idx = i
                break
        if hdr_idx == -1:
            continue

        reader = csv.DictReader(lines[hdr_idx:])
        for record in reader:
            run_date = (record.get("Run Date") or "").strip()
            if not run_date or not run_date[0].isdigit():
                continue
            # Build canonical row dict
            row = {col: (record.get(col) or "").strip().strip('"') for col in out_cols}
            key = tuple(row[c] for c in out_cols)
            # Skip only if the exact same row was already seen in a DIFFERENT file
            prev_file = cross_file_seen.get(key)
            if prev_file is not None and prev_file != csv_path.name:
                continue
            cross_file_seen[key] = csv_path.name
            parsed.append(row)

    if not parsed:
        print("  ERROR: No valid rows found in CSVs")
        sys.exit(1)

    # Sort by date descending (newest first, matching Fidelity export order)
    def _sort_key(r: dict[str, str]) -> str:
        d = r["Run Date"]
        return d[6:10] + d[0:2] + d[3:5] if len(d) == 10 else d

    parsed.sort(key=_sort_key, reverse=True)

    # Write merged file
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    merged = FIDELITY_CSV
    with merged.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_cols)
        writer.writeheader()
        writer.writerows(parsed)
    print(f"  Merged {len(parsed)} unique rows -> {merged.name}")
    return merged


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


def _run_validation() -> None:
    """Run post-build validation and exit on FATAL issues."""
    print("[V] Validating build...")
    issues = validate_build(DB_PATH)
    fatals = [i for i in issues if i.severity == Severity.FATAL]
    warnings = [i for i in issues if i.severity == Severity.WARNING]
    for w in warnings:
        print(f"  WARNING: {w.name}: {w.message}")
    if fatals:
        for f in fatals:
            print(f"  FATAL: {f.name}: {f.message}")
        print(f"\n  Build validation FAILED ({len(fatals)} fatal). Sync blocked.")
        sys.exit(1)
    print(f"  Passed ({len(warnings)} warnings)")


# ── Main ─────────────────────────────────────────────────────────────────────


def _ingest_and_fetch(config, start, end, csv_path: Path):
    """Steps 1-4: init DB, ingest sources, fetch prices. Returns k401_daily."""
    # ── Step 1: Initialise database ──
    print("\n[1] Initialising database...")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    init_db(DB_PATH)

    # ── Step 2: Ingest Fidelity ──
    print("[2] Ingesting Fidelity transactions...")
    count = ingest_fidelity_csv(DB_PATH, csv_path)
    print(f"  {count} rows")

    # ── Step 3: Ingest Empower QFX ──
    print("[3] Ingesting Empower 401k...")
    qfx_files = sorted(DOWNLOADS.glob("Bloomberg.Download*.qfx"))
    for qfx_path in qfx_files:
        ingest_empower_qfx(DB_PATH, qfx_path)
    qfx_contribs = load_all_contributions(DOWNLOADS)
    if qfx_contribs:
        ingest_empower_contributions(DB_PATH, qfx_contribs)

    # ── Step 4: Fetch prices ──
    print("[4] Fetching prices...")
    periods = symbol_holding_periods(csv_path)
    qfx_snaps = load_all_qfx(DOWNLOADS)
    proxy_start = qfx_snaps[0].date if qfx_snaps else start
    for proxy in PROXY_TICKERS.values():
        existing = periods.get(proxy)
        if existing is None or existing[0] > proxy_start:
            periods[proxy] = (proxy_start, None)
    # Add market index tickers for /market endpoint
    for idx_ticker in ("^GSPC", "^NDX", "000300.SS"):
        periods[idx_ticker] = (start, None)

    # Add Robinhood symbols that aren't in Fidelity
    if ROBINHOOD_CSV.exists():
        from generate_asset_snapshot.ingest.robinhood_history import load_robinhood_csv
        rh_syms = {r["instrument"] for r in load_robinhood_csv(ROBINHOOD_CSV) if r["instrument"]}
        for sym in rh_syms - set(periods.keys()):
            periods[sym] = (start, None)

    fetch_and_store_prices(DB_PATH, periods, end)
    fetch_and_store_cny_rates(DB_PATH, start, end)

    # ── Prepare 401k daily values ──
    proxy_prices = load_proxy_prices(DB_PATH, PROXY_TICKERS)
    last_qfx_date = qfx_snaps[-1].date if qfx_snaps else None
    k401_contribs = _load_401k_contributions(qfx_contribs, last_qfx_date)
    k401_daily = daily_401k_values(qfx_snaps, proxy_prices, start, end, contributions=k401_contribs)

    return k401_daily


def _print_summary(alloc):
    if not alloc:
        return
    earliest, latest = alloc[0], alloc[-1]
    print(f"\n  Earliest: {earliest['date']}  ${_f(earliest['total']):,.0f}")
    print(f"  Latest:   {latest['date']}  ${_f(latest['total']):,.0f}")


# ── Full rebuild ────────────────────────────────────────────────────────────


def _full_build(config, start, end, k401_daily, csv_path: Path):
    print("\n[5] Computing full allocation...")
    alloc = compute_daily_allocation(DB_PATH, DEFAULT_QJ_DB, config, k401_daily, start, end, robinhood_csv=ROBINHOOD_CSV)
    print(f"  {len(alloc)} daily records")

    print("[6] Writing computed_daily...")
    conn = get_connection(DB_PATH)
    try:
        conn.execute("DELETE FROM computed_daily")
        conn.execute("DELETE FROM computed_daily_tickers")
        for r in alloc:
            conn.execute(
                "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net, liabilities)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (r["date"], _f(r["total"]), _f(r["us_equity"]), _f(r["non_us_equity"]),
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
        conn.commit()
    finally:
        conn.close()

    # Ingest Qianji transactions for /cashflow endpoint
    from generate_asset_snapshot.db import ingest_qianji_transactions

    qianji_records, _ = load_all_from_db(DEFAULT_QJ_DB)
    qj_count = ingest_qianji_transactions(DB_PATH, qianji_records)
    print(f"  {qj_count} Qianji transactions ingested")

    # Precompute market index data
    print("[M] Precomputing market data...")
    precompute_market(DB_PATH)
    print("  Done")

    # Precompute holdings detail
    print("[H] Precomputing holdings detail...")
    precompute_holdings_detail(DB_PATH)
    print("  Done")

    _print_summary(alloc)

    if "--no-validate" not in sys.argv:
        _run_validation()

    return alloc


# ── Incremental ─────────────────────────────────────────────────────────────


def _incremental_build(config, start, end, k401_daily, csv_path: Path):
    last = get_last_computed_date(DB_PATH)
    if last is None:
        print("  No existing data — falling back to full build")
        return _full_build(config, start, end, k401_daily, csv_path)

    inc_start = last + timedelta(days=1)
    if inc_start > end:
        print(f"  Already up to date (last: {last})")
        return []

    print(f"\n[5] Computing allocation {inc_start} -> {end} (incremental)...")
    alloc = compute_daily_allocation(DB_PATH, DEFAULT_QJ_DB, config, k401_daily, inc_start, end, robinhood_csv=ROBINHOOD_CSV)
    print(f"  {len(alloc)} new daily records")

    if alloc:
        print("[6] Appending to computed_daily...")
        added = append_daily(DB_PATH, alloc)
        print(f"  {added} rows appended")

    # Precompute market index data (always refresh on incremental)
    print("[M] Precomputing market data...")
    precompute_market(DB_PATH)
    print("  Done")

    # Precompute holdings detail (always refresh on incremental)
    print("[H] Precomputing holdings detail...")
    precompute_holdings_detail(DB_PATH)
    print("  Done")

    _print_summary(alloc)

    if "--no-validate" not in sys.argv:
        _run_validation()

    return alloc


# ── Verify ──────────────────────────────────────────────────────────────────


def _verify_build(config, start, end, k401_daily, csv_path: Path):
    print("\n[5] Computing full allocation for verification...")
    alloc = compute_daily_allocation(DB_PATH, DEFAULT_QJ_DB, config, k401_daily, start, end, robinhood_csv=ROBINHOOD_CSV)
    print(f"  {len(alloc)} daily records recomputed")

    print("[V] Cross-checking against persisted data...")
    drifts = verify_daily(DB_PATH, alloc)
    if not drifts:
        print("  ✓ No drift detected")
    else:
        print(f"  ✗ {len(drifts)} drifts found:")
        for d in drifts[:20]:
            print(f"    {d.date} {d.field}: persisted={d.persisted:,.2f} recomputed={d.recomputed:,.2f} Δ={d.delta:+,.2f}")
        if len(drifts) > 20:
            print(f"    ... and {len(drifts) - 20} more")
    return alloc, drifts


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    mode = "full"
    if "--incremental" in sys.argv:
        mode = "incremental"
    elif "--verify" in sys.argv:
        mode = "verify"

    print("=" * 60)
    print(f"  Timemachine DB Builder  [{mode}]")
    print("=" * 60)

    config = _load_config(CONFIG_PATH)

    # Resolve Fidelity CSV: --csv <path>, auto-merge from Downloads, or existing file
    csv_path = _merge_fidelity_csvs()

    rows = _load_raw_rows(csv_path)
    start = min(_parse_date(r["Run Date"]) for r in rows)
    end = date.today()
    print(f"  Range: {start} -> {end}")

    k401_daily = _ingest_and_fetch(config, start, end, csv_path)

    if mode == "full":
        _full_build(config, start, end, k401_daily, csv_path)
    elif mode == "incremental":
        _incremental_build(config, start, end, k401_daily, csv_path)
    elif mode == "verify":
        _verify_build(config, start, end, k401_daily, csv_path)

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
