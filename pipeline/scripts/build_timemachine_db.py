"""Build the timemachine SQLite database from raw data sources.

Integration script that:
  1. Initialises data/timemachine.db with all tables
  2. Ingests Fidelity brokerage transactions from CSV
  3. Ingests Empower 401k quarterly snapshots + contributions from QFX files
  4. Fetches and stores prices + CNY rates in timemachine.db.daily_close
  5. Computes daily allocation (reads prices from DB)
  6. Stores results

Refreshes the last ``REFRESH_WINDOW_DAYS`` of ``computed_daily`` on every
run, plus fills any historical gap beyond the window. If the DB is missing
or empty, a full build runs automatically. To force a clean rebuild, delete
``pipeline/data/timemachine.db`` before running.

Usage:
  python scripts/build_timemachine_db.py [--csv PATH] [--no-validate]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# Ensure the pipeline package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import etl.dotenv_loader  # noqa: E402, F401  (side effect: load pipeline/.env)
from etl.allocation import compute_daily_allocation
from etl.categories import ingest_categories
from etl.db import (
    get_connection,
    get_last_computed_date,
    init_db,
    upsert_daily_rows,
)
from etl.ingest.empower_401k import (
    ingest_empower_contributions,
    ingest_empower_qfx,
)
from etl.ingest.fidelity_history import ingest_fidelity_csv
from etl.ingest.qianji_db import ingest_qianji_transactions, load_all_from_db
from etl.k401 import (
    PROXY_TICKERS,
    Contribution,
    QuarterSnapshot,
    daily_401k_values,
    load_all_contributions,
    load_all_qfx,
)
from etl.migrations.add_fidelity_action_kind import migrate as _migrate_fidelity_action_kind
from etl.precompute import (
    precompute_holdings_detail,
    precompute_market,
)
from etl.prices import (
    fetch_and_store_cny_rates,
    fetch_and_store_prices,
    load_proxy_prices,
    symbol_holding_periods_from_db,
)
from etl.refresh import refresh_window_start
from etl.sources import build_investment_sources
from etl.sources import fidelity as _fidelity_source_module  # noqa: F401 — import side-effect registers FidelitySource
from etl.timemachine import DEFAULT_QJ_DB
from etl.types import AllocationRow, RawConfig
from etl.validate import Severity, validate_build

# ── Paths ────────────────────────────────────────────────────────────────────

PIPELINE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class BuildPaths:
    """Resolved filesystem paths for a single build invocation.

    ``db_path`` and ``robinhood_csv`` are derived from ``data_dir`` /
    ``downloads`` rather than stored, so callers can't introduce a
    divergence by passing, e.g., a custom ``data_dir`` with the wrong
    ``db_path``.
    """
    data_dir: Path
    config: Path
    downloads: Path
    csv: Path | None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "timemachine.db"

    @property
    def robinhood_csv(self) -> Path:
        return self.downloads / "Robinhood_history.csv"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Build the timemachine SQLite database")
    parser.add_argument("--csv", type=Path, help="Path to a specific Fidelity CSV file")
    parser.add_argument("--no-validate", action="store_true", help="Skip post-build validation")
    parser.add_argument("--data-dir", type=Path, default=None, help="Override data directory (default: pipeline/data/)")
    parser.add_argument("--config", type=Path, default=None, help="Override config.json path")
    parser.add_argument("--downloads", type=Path, default=None, help="Override downloads directory")
    parser.add_argument(
        "--prices-from-csv",
        type=Path,
        default=None,
        help="Read prices from this CSV instead of Yahoo. "
             "CSV columns: date (YYYY-MM-DD) + one column per ticker. For test fixtures only.",
    )
    parser.add_argument(
        "--dry-run-market",
        action="store_true",
        help="Skip Yahoo market-index fetches (used with --prices-from-csv for offline regression fixtures).",
    )
    return parser.parse_args(argv)


def _resolve_paths(args: argparse.Namespace) -> BuildPaths:
    """Resolve all file paths from parsed args and environment variables."""
    data_dir = args.data_dir or Path(os.environ.get("PORTAL_DATA_DIR", PIPELINE_DIR / "data"))
    config = args.config or Path(os.environ.get("PORTAL_CONFIG", PIPELINE_DIR / "config.json"))
    downloads = args.downloads or Path(os.environ.get("PORTAL_DOWNLOADS", Path.home() / "Downloads"))
    return BuildPaths(
        data_dir=data_dir,
        config=config,
        downloads=downloads,
        csv=args.csv,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


def _load_prices_from_csv(db_path: Path, csv_path: Path) -> None:
    """Load prices from a CSV into daily_close, bypassing Yahoo.

    CSV format: ``date`` column (YYYY-MM-DD) plus one column per ticker.
    Empty cells are skipped. Used for offline regression fixtures — real builds
    still fetch from Yahoo via :func:`fetch_and_store_prices`.
    """
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        return

    symbols = [c for c in (reader.fieldnames or []) if c and c != "date"]
    conn = get_connection(db_path)
    try:
        for row in rows:
            date_iso = (row.get("date") or "").strip()
            if not date_iso:
                continue
            for sym in symbols:
                raw = (row.get(sym) or "").strip()
                if not raw:
                    continue
                try:
                    close = float(raw)
                except ValueError:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO daily_close (symbol, date, close) VALUES (?, ?, ?)",
                    (sym, date_iso, close),
                )
        conn.commit()
    finally:
        conn.close()


def _load_config(path: Path) -> RawConfig:
    """Parse config.json into a typed ``RawConfig`` TypedDict.

    Validation is best-effort: TypedDict doesn't enforce structure at runtime,
    but downstream typed access via ``.get()`` + TypedDict field types gives
    mypy enough narrowing to drop the ``cast()`` calls that used to surround
    every field read.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"Config root must be an object, got {type(data).__name__}"
        raise ValueError(msg)
    return data  # type: ignore[return-value]  # shape-validated at runtime by `etl.config.validate_config` on first downstream use


def _ingest_fidelity_csvs(paths: BuildPaths) -> None:
    """Ingest all Fidelity CSVs from Downloads directly into the database.

    Each CSV covers a date range. ``ingest_fidelity_csv()`` handles overlap
    by deleting existing rows in the CSV's date range before inserting —
    so processing files in chronological order naturally deduplicates.
    """
    # Use explicit --csv path if provided
    if paths.csv is not None:
        if not paths.csv.exists():
            print(f"  ERROR: --csv file not found: {paths.csv}")
            sys.exit(1)
        print(f"  Using single CSV: {paths.csv}")
        ingest_fidelity_csv(paths.db_path, paths.csv)
        return

    # Scan downloads directory for Accounts_History*.csv
    raw_csvs = sorted(paths.downloads.glob("Accounts_History*.csv"))
    if not raw_csvs:
        print(f"  ERROR: No Accounts_History CSVs found in {paths.downloads}")
        sys.exit(1)

    # Sort by earliest date in each file (chronological ingestion).
    # Raw Fidelity CSVs carry MM/DD/YYYY; convert to YYYYMMDD for a
    # lexicographically sortable key.
    def _csv_start_date(path: Path) -> str:
        """Return earliest YYYYMMDD date in a CSV for sorting."""
        text = path.read_text(encoding="utf-8-sig")
        import re
        dates: list[str] = re.findall(r"(\d{2}/\d{2}/\d{4})", text)
        if not dates:
            return "99999999"
        return min(d[6:10] + d[0:2] + d[3:5] for d in dates)

    raw_csvs.sort(key=_csv_start_date)
    print(f"  Found {len(raw_csvs)} CSVs in {paths.downloads}, ingesting chronologically...")

    total = 0
    for csv_path in raw_csvs:
        count = ingest_fidelity_csv(paths.db_path, csv_path)
        print(f"    {csv_path.name}: {count} total rows")
        total = count

    print(f"  {total} rows after ingestion")


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


def _run_validation(paths: BuildPaths) -> None:
    """Run post-build validation and exit on FATAL issues."""
    print("[V] Validating build...")
    issues = validate_build(paths.db_path)
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


def _derive_start_date(paths: BuildPaths, fallback: date) -> date:
    """Derive build start date from earliest Fidelity transaction (ISO run_date)."""
    conn = get_connection(paths.db_path)
    try:
        row = conn.execute(
            "SELECT MIN(run_date) FROM fidelity_transactions"
        ).fetchone()
    finally:
        conn.close()
    return date.fromisoformat(row[0]) if row and row[0] else fallback


# ── Ingest & fetch pipeline ──────────────────────────────────────────────────


def _init_db_and_ingest_sources(
    paths: BuildPaths,
    config: RawConfig,
) -> tuple[list[QuarterSnapshot], list[Contribution]]:
    """Steps 1-3: init DB, ingest Fidelity + Empower 401k sources.

    Loads QFX snapshots + contributions from disk and ingests the latter. The
    loaded objects are returned so later steps can reuse them without a second
    disk read.
    """
    # ── Step 1: Initialise database ──
    print("\n[1] Initialising database...")
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    init_db(paths.db_path)

    # ── Step 1b: Category metadata (target weights + display order) ──
    ingest_categories(paths.db_path, config)

    # ── Step 2: Ingest Fidelity ──
    # Run the action_kind migration *before* ingest so legacy DBs (pre the
    # 2026-04 data-source abstraction refactor) have the column available for
    # ingest_fidelity_csv's INSERT. The migration simultaneously backfills
    # any pre-existing rows that predate the column. Idempotent — no-op on
    # fresh DBs (the column already exists via init_db's DDL) and on already-
    # classified rows.
    _migrate_fidelity_action_kind(paths.db_path)
    print("[2] Ingesting Fidelity transactions...")
    _ingest_fidelity_csvs(paths)

    # ── Step 3: Ingest Empower QFX ──
    print("[3] Ingesting Empower 401k...")
    qfx_files = sorted(paths.downloads.glob("Bloomberg.Download*.qfx"))
    for qfx_path in qfx_files:
        ingest_empower_qfx(paths.db_path, qfx_path)
    qfx_contribs = load_all_contributions(paths.downloads)
    if qfx_contribs:
        ingest_empower_contributions(paths.db_path, qfx_contribs)

    qfx_snaps = load_all_qfx(paths.downloads)
    return qfx_snaps, qfx_contribs


def _compute_holding_periods(
    paths: BuildPaths,
    end: date,
    qfx_snaps: list[QuarterSnapshot],
) -> tuple[dict[str, tuple[date, date | None]], date]:
    """Derive the symbol → (start, end) map used to bulk-fetch prices.

    Union of:
      - Fidelity holding periods from the DB
      - 401k proxy tickers (extended back to the first QFX snapshot)
      - Market-index tickers for the /market endpoint
      - Robinhood symbols not already in Fidelity
    """
    periods = symbol_holding_periods_from_db(paths.db_path)
    # Earliest date from all holding periods
    earliest = min((p[0] for p in periods.values()), default=end)

    proxy_start = qfx_snaps[0].date if qfx_snaps else earliest
    for proxy in PROXY_TICKERS.values():
        existing = periods.get(proxy)
        if existing is None or existing[0] > proxy_start:
            periods[proxy] = (proxy_start, None)
    # Add market index tickers for /market endpoint
    for idx_ticker in ("^GSPC", "^NDX", "000300.SS"):
        periods[idx_ticker] = (earliest, None)

    # Add Robinhood symbols that aren't in Fidelity
    if paths.robinhood_csv.exists():
        from etl.ingest.robinhood_history import load_robinhood_csv
        rh_syms = {r["instrument"] for r in load_robinhood_csv(paths.robinhood_csv) if r["instrument"]}
        for sym in rh_syms - set(periods.keys()):
            periods[sym] = (earliest, None)

    return periods, earliest


def _fetch_all_prices(
    paths: BuildPaths,
    periods: dict[str, tuple[date, date | None]],
    earliest: date,
    end: date,
    *,
    prices_from_csv: Path | None = None,
) -> None:
    """Bulk-fetch + persist ticker prices and CNY rates for the given periods.

    ``earliest`` is the earliest first-held date across symbols (from
    ``symbol_holding_periods_from_db``), which is the appropriate bound for
    ticker-price fetching. CNY=X is needed earlier — from the first Fidelity
    transaction overall (e.g. a cash deposit that happens before any buy) —
    because allocation converts CNY-denominated balances from day one. We
    therefore derive the CNY start from ``MIN(run_date)`` directly.

    When ``prices_from_csv`` is set, loads prices from that CSV instead of
    Yahoo (for offline regression fixtures). Yahoo is still skipped for CNY
    too — callers provide all needed series in the CSV.
    """
    if prices_from_csv is not None:
        print(f"  Loading prices from CSV (Yahoo skipped): {prices_from_csv}")
        _load_prices_from_csv(paths.db_path, prices_from_csv)
        return

    _conn = get_connection(paths.db_path)
    # Use computed_daily start as global_start so ticker charts cover the full brush range
    cd_start_row = _conn.execute("SELECT MIN(date) FROM computed_daily").fetchone()
    # First Fidelity transaction (not just first buy) — drives CNY fetch lower bound.
    first_txn_row = _conn.execute("SELECT MIN(run_date) FROM fidelity_transactions").fetchone()
    _conn.close()
    global_start = date.fromisoformat(cd_start_row[0]) if cd_start_row and cd_start_row[0] else earliest
    cny_start = (
        date.fromisoformat(first_txn_row[0])
        if first_txn_row and first_txn_row[0]
        else earliest
    )
    fetch_and_store_prices(paths.db_path, periods, end, global_start=global_start)
    fetch_and_store_cny_rates(paths.db_path, cny_start, end)


def _compute_401k_daily(
    paths: BuildPaths,
    qfx_snaps: list[QuarterSnapshot],
    qfx_contribs: list[Contribution],
    earliest: date,
    end: date,
) -> dict[date, dict[str, float]]:
    """Build the per-day 401k value map from proxy prices + merged contributions."""
    proxy_prices = load_proxy_prices(paths.db_path, PROXY_TICKERS)
    last_qfx_date = qfx_snaps[-1].date if qfx_snaps else None
    k401_contribs = _load_401k_contributions(qfx_contribs, last_qfx_date)
    return daily_401k_values(qfx_snaps, proxy_prices, earliest, end, contributions=k401_contribs)


# ── Main ─────────────────────────────────────────────────────────────────────


def _ingest_and_fetch(
    paths: BuildPaths,
    config: RawConfig,
    end: date,
    *,
    prices_from_csv: Path | None = None,
) -> dict[date, dict[str, float]]:
    """Steps 1-5: init DB, ingest sources, fetch prices, build 401k daily map."""
    qfx_snaps, qfx_contribs = _init_db_and_ingest_sources(paths, config)

    print("[4] Fetching prices...")
    periods, earliest = _compute_holding_periods(paths, end, qfx_snaps)
    _fetch_all_prices(paths, periods, earliest, end, prices_from_csv=prices_from_csv)

    return _compute_401k_daily(paths, qfx_snaps, qfx_contribs, earliest, end)


def _print_summary(alloc: list[AllocationRow]) -> None:
    if not alloc:
        return
    earliest, latest = alloc[0], alloc[-1]
    print(f"\n  Earliest: {earliest['date']}  ${earliest['total']:,.0f}")
    print(f"  Latest:   {latest['date']}  ${latest['total']:,.0f}")


# ── Full rebuild ────────────────────────────────────────────────────────────


def _full_build(
    paths: BuildPaths,
    config: RawConfig,
    start: date,
    end: date,
    k401_daily: dict[date, dict[str, float]],
    *,
    no_validate: bool = False,
    dry_run_market: bool = False,
) -> list[AllocationRow]:
    print("\n[5] Computing full allocation...")
    raw_sources_cfg = dict(config) | {"fidelity_downloads": paths.downloads}
    investment_sources = build_investment_sources(raw_sources_cfg, paths.db_path)
    alloc = compute_daily_allocation(
        paths.db_path, DEFAULT_QJ_DB, config, k401_daily, start, end,
        robinhood_csv=paths.robinhood_csv,
        investment_sources=investment_sources,
    )
    print(f"  {len(alloc)} daily records")

    print("[6] Writing computed_daily...")
    conn = get_connection(paths.db_path)
    try:
        conn.execute("DELETE FROM computed_daily")
        conn.execute("DELETE FROM computed_daily_tickers")
        for r in alloc:
            conn.execute(
                "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net, liabilities)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (r["date"], r["total"], r["us_equity"], r["non_us_equity"],
                 r["crypto"], r["safe_net"], r["liabilities"]),
            )
            for t in r["tickers"]:
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
    qianji_records, _ = load_all_from_db(DEFAULT_QJ_DB)
    retirement_cats = list(config.get("retirement_income_categories") or [])
    qj_count = ingest_qianji_transactions(
        paths.db_path, qianji_records, retirement_categories=retirement_cats,
    )
    print(f"  {qj_count} Qianji transactions ingested")

    if dry_run_market:
        print("[M] Market precompute skipped (--dry-run-market)")
        print("[H] Holdings detail precompute skipped (--dry-run-market)")
    else:
        # Precompute market index data
        print("[M] Precomputing market data...")
        precompute_market(paths.db_path)
        print("  Done")

        # Precompute holdings detail
        print("[H] Precomputing holdings detail...")
        precompute_holdings_detail(paths.db_path)
        print("  Done")

    _print_summary(alloc)

    if not no_validate:
        _run_validation(paths)

    return alloc


# ── Incremental ─────────────────────────────────────────────────────────────


def compute_inc_start(last: date, start: date, end: date) -> date:
    """The first date the incremental build should recompute.

    Always reaches back into the refresh-window tail so today's moving
    snapshot and late Yahoo corrections land in ``computed_daily``; also
    fills any historical gap if ``last`` sits further back than the tail
    (e.g., after a long absence). The ``min()`` covers the gap case; the
    outer ``max()`` clamps to the configured ``start``. Caller checks
    whether the returned value exceeds ``end`` (meaning: nothing to do).

    Public so tests can exercise it without building a full DB fixture.
    """
    refresh_floor = refresh_window_start(end)
    return max(start, min(last + timedelta(days=1), refresh_floor))


def _build_refresh_window(
    paths: BuildPaths,
    config: RawConfig,
    start: date,
    end: date,
    k401_daily: dict[date, dict[str, float]],
    *,
    no_validate: bool = False,
    dry_run_market: bool = False,
) -> list[AllocationRow]:
    """Recompute the REFRESH_WINDOW_DAYS tail of ``computed_daily``, filling any
    historical gap beyond the tail. Delegates to ``_full_build`` when the DB
    has no prior rows (first run, or after a manual reset)."""
    last = get_last_computed_date(paths.db_path)
    if last is None:
        print("  No existing data — falling back to full build")
        return _full_build(
            paths, config, start, end, k401_daily,
            no_validate=no_validate, dry_run_market=dry_run_market,
        )

    inc_start = compute_inc_start(last, start, end)
    if inc_start > end:
        print(f"  Already up to date (last: {last})")
        return []

    print(f"\n[5] Computing allocation {inc_start} -> {end} (incremental)...")
    raw_sources_cfg = dict(config) | {"fidelity_downloads": paths.downloads}
    investment_sources = build_investment_sources(raw_sources_cfg, paths.db_path)
    alloc = compute_daily_allocation(
        paths.db_path, DEFAULT_QJ_DB, config, k401_daily, inc_start, end,
        robinhood_csv=paths.robinhood_csv,
        investment_sources=investment_sources,
    )
    print(f"  {len(alloc)} daily records")

    if alloc:
        print("[6] Upserting to computed_daily...")
        written = upsert_daily_rows(paths.db_path, alloc)
        print(f"  {written} rows written")

    if dry_run_market:
        print("[M] Market precompute skipped (--dry-run-market)")
        print("[H] Holdings detail precompute skipped (--dry-run-market)")
    else:
        # Precompute market index data (always refresh on incremental)
        print("[M] Precomputing market data...")
        precompute_market(paths.db_path)
        print("  Done")

        # Precompute holdings detail (always refresh on incremental)
        print("[H] Precomputing holdings detail...")
        precompute_holdings_detail(paths.db_path)
        print("  Done")

    _print_summary(alloc)

    if not no_validate:
        _run_validation(paths)

    return alloc


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    args = _parse_args()
    paths = _resolve_paths(args)

    print("=" * 60)
    print("  Timemachine DB Builder")
    print("=" * 60)

    config = _load_config(paths.config)
    end = date.today()

    # Ingest all sources, fetch prices (populates DB)
    k401_daily = _ingest_and_fetch(
        paths, config, end, prices_from_csv=args.prices_from_csv,
    )

    # Derive date range from ingested fidelity transactions
    start = _derive_start_date(paths, fallback=end)
    print(f"  Range: {start} -> {end}")

    _build_refresh_window(
        paths, config, start, end, k401_daily,
        no_validate=args.no_validate,
        dry_run_market=args.dry_run_market,
    )

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
