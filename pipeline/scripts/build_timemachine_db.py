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
from etl.ingest.qianji_db import ingest_qianji_transactions, load_all_from_db
from etl.migrations.add_fidelity_action_kind import migrate as _migrate_fidelity_action_kind
from etl.migrations.drop_robinhood_unique import migrate as _migrate_drop_robinhood_unique
from etl.precompute import (
    precompute_holdings_detail,
    precompute_market,
)
from etl.prices import (
    fetch_and_store_cny_rates,
    fetch_and_store_prices,
    symbol_holding_periods_from_db,
)
from etl.refresh import refresh_window_start
from etl.sources import InvestmentSource, build_investment_sources
from etl.sources import empower as _empower_source_module  # noqa: F401 — import side-effect registers EmpowerSource
from etl.sources import fidelity as _fidelity_source_module  # noqa: F401 — import side-effect registers FidelitySource
from etl.sources import (
    robinhood as _robinhood_source_module,  # noqa: F401 — import side-effect registers RobinhoodSource
)
from etl.sources.empower import PROXY_TICKERS, Contribution, EmpowerSource, EmpowerSourceConfig
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
    """Ingest all Fidelity CSVs via :class:`FidelitySource`.

    Each CSV covers a date range. :meth:`FidelitySource._ingest_one_csv`
    handles overlap by deleting existing rows in the CSV's date range
    before inserting — so processing files in chronological order (the
    order ``FidelitySource.ingest`` already uses) naturally deduplicates.
    """
    from etl.sources.fidelity import (
        FidelitySource,
        FidelitySourceConfig,
        _csv_earliest_date,
    )

    # Use explicit --csv path if provided. Build a minimal source instance
    # just to reuse the per-file ingest method.
    if paths.csv is not None:
        if not paths.csv.exists():
            print(f"  ERROR: --csv file not found: {paths.csv}")
            sys.exit(1)
        print(f"  Using single CSV: {paths.csv}")
        single_src = FidelitySource(
            FidelitySourceConfig(
                downloads_dir=paths.downloads,
                fidelity_accounts={},
                mutual_funds=frozenset(),
            ),
            paths.db_path,
        )
        single_src._ingest_one_csv(paths.csv)
        return

    raw_csvs = sorted(paths.downloads.glob("Accounts_History*.csv"))
    if not raw_csvs:
        print(f"  ERROR: No Accounts_History CSVs found in {paths.downloads}")
        sys.exit(1)

    raw_csvs.sort(key=_csv_earliest_date)
    print(f"  Found {len(raw_csvs)} CSVs in {paths.downloads}, ingesting chronologically...")

    bulk_src = FidelitySource(
        FidelitySourceConfig(
            downloads_dir=paths.downloads,
            fidelity_accounts={},
            mutual_funds=frozenset(),
        ),
        paths.db_path,
    )
    total = 0
    for csv_path in raw_csvs:
        count = bulk_src._ingest_one_csv(csv_path)
        print(f"    {csv_path.name}: {count} total rows")
        total = count

    print(f"  {total} rows after ingestion")


def _ingest_robinhood_csv(paths: BuildPaths) -> None:
    """Ingest the Robinhood activity CSV via :class:`RobinhoodSource`.

    Silent no-op when the CSV is absent (user has no Robinhood holdings) —
    :meth:`RobinhoodSource.ingest` handles the missing-file case internally.
    """
    from etl.sources.robinhood import RobinhoodSource, RobinhoodSourceConfig

    src = RobinhoodSource(
        RobinhoodSourceConfig(csv_path=paths.robinhood_csv),
        paths.db_path,
    )
    src.ingest()


def _qianji_401k_fallback_contribs(last_qfx_date: date | None) -> list[Contribution]:
    """Read Qianji 401k contributions made *after* the last QFX snapshot.

    QFX carries per-fund CUSIPs, so contributions sourced from QFX know their
    exact fund. Qianji only records a total amount — used as fallback for
    periods without QFX coverage (e.g. pre the next quarterly export).
    For Qianji fallback we split 50/50 between ``401k sp500`` and
    ``401k ex-us`` (matches the user's current allocation).
    """
    contribs: list[Contribution] = []
    if not last_qfx_date or not DEFAULT_QJ_DB.exists():
        return contribs
    conn = sqlite3.connect(f"file:{DEFAULT_QJ_DB}?mode=ro", uri=True)
    try:
        for money, ts in conn.execute(
            "SELECT money, time FROM user_bill WHERE status = 1 AND type = 1 AND fromact = '401k' ORDER BY time"
        ):
            d = datetime.fromtimestamp(ts, tz=UTC).date()
            if d > last_qfx_date:
                amt = float(money)
                contribs.append(Contribution(date=d, amount=amt * 0.5, ticker="401k sp500"))
                contribs.append(Contribution(date=d, amount=amt * 0.5, ticker="401k ex-us"))
    finally:
        conn.close()
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
) -> None:
    """Steps 1-3: init DB, ingest Fidelity + Robinhood + Empower 401k sources.

    Every source persists its raw inputs into ``timemachine.db`` at this stage;
    per-day valuation (step 5) reads exclusively from the DB via the
    :class:`InvestmentSource` registry.
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

    # ── Step 2b: Ingest Robinhood ──
    # The original Task-17 schema carried a UNIQUE(txn_date, ticker, action,
    # quantity, amount_usd) constraint for idempotent re-ingest, but that
    # silently collapsed legitimate same-day duplicate trades (breaking L1
    # parity with legacy ``replay_robinhood``). Idempotency now comes from
    # :meth:`RobinhoodSource.ingest`'s range-replace (DELETE within CSV's
    # [min_date, max_date] + INSERT everything) — identical to Fidelity.
    # Migration below is a no-op on fresh DBs (schema already correct).
    _migrate_drop_robinhood_unique(paths.db_path)
    print("[2b] Ingesting Robinhood transactions...")
    _ingest_robinhood_csv(paths)

    # ── Step 3: Ingest Empower QFX + Qianji fallback contributions ──
    print("[3] Ingesting Empower 401k...")
    empower_src = EmpowerSource(
        EmpowerSourceConfig(downloads_dir=paths.downloads), paths.db_path,
    )
    empower_src.ingest()

    # QFX coverage ends at the latest snapshot date. Contributions made after
    # that (as recorded in Qianji) are tracked as 50/50 sp500/ex-us fallback
    # rows and persisted into ``empower_contributions`` so that
    # :meth:`EmpowerSource.positions_at` sees them at query time.
    last_qfx_date = _last_empower_snapshot_date(paths.db_path)
    fallback_contribs = _qianji_401k_fallback_contribs(last_qfx_date)
    if fallback_contribs:
        empower_src.ingest_contributions(fallback_contribs)


def _last_empower_snapshot_date(db_path: Path) -> date | None:
    """Return the most recent Empower snapshot date, or None if no snapshots exist."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT MAX(snapshot_date) FROM empower_snapshots").fetchone()
    finally:
        conn.close()
    return date.fromisoformat(row[0]) if row and row[0] else None


def _first_empower_snapshot_date(db_path: Path) -> date | None:
    """Return the earliest Empower snapshot date, or None if no snapshots exist."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT MIN(snapshot_date) FROM empower_snapshots").fetchone()
    finally:
        conn.close()
    return date.fromisoformat(row[0]) if row and row[0] else None


def _compute_holding_periods(
    paths: BuildPaths,
    end: date,
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

    first_snap = _first_empower_snapshot_date(paths.db_path)
    proxy_start = first_snap if first_snap is not None else earliest
    for proxy in PROXY_TICKERS.values():
        existing = periods.get(proxy)
        if existing is None or existing[0] > proxy_start:
            periods[proxy] = (proxy_start, None)
    # Add market index tickers for /market endpoint
    for idx_ticker in ("^GSPC", "^NDX", "000300.SS"):
        periods[idx_ticker] = (earliest, None)

    # Add Robinhood symbols that aren't in Fidelity — query the
    # ``robinhood_transactions`` table that :class:`RobinhoodSource.ingest`
    # populated in step 3b. (Reading from the DB here instead of the CSV
    # means a user whose CSV was deleted after an earlier build still has
    # their prices refetched.)
    conn = get_connection(paths.db_path)
    try:
        rh_syms = {
            sym for (sym,) in conn.execute(
                "SELECT DISTINCT ticker FROM robinhood_transactions WHERE ticker != ''"
            )
        }
    finally:
        conn.close()
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


# ── Main ─────────────────────────────────────────────────────────────────────


def _ingest_and_fetch(
    paths: BuildPaths,
    config: RawConfig,
    end: date,
    *,
    prices_from_csv: Path | None = None,
) -> None:
    """Steps 1-4: init DB, ingest every source, fetch prices.

    Per-day 401k valuation used to be pre-computed here and threaded through
    to :func:`compute_daily_allocation`. After the Phase 5 migration, Empower
    is a full :class:`InvestmentSource` that reads its own DB tables at
    query time — so this function no longer needs to compute anything.
    """
    _init_db_and_ingest_sources(paths, config)

    print("[4] Fetching prices...")
    periods, earliest = _compute_holding_periods(paths, end)
    _fetch_all_prices(paths, periods, earliest, end, prices_from_csv=prices_from_csv)


def _print_summary(alloc: list[AllocationRow]) -> None:
    if not alloc:
        return
    earliest, latest = alloc[0], alloc[-1]
    print(f"\n  Earliest: {earliest['date']}  ${earliest['total']:,.0f}")
    print(f"  Latest:   {latest['date']}  ${latest['total']:,.0f}")


# ── Full rebuild ────────────────────────────────────────────────────────────


def _build_all_sources(paths: BuildPaths, config: RawConfig) -> list[InvestmentSource]:
    """Build the full set of :class:`InvestmentSource` instances for this run."""
    raw_sources_cfg = dict(config) | {
        "fidelity_downloads": paths.downloads,
        "robinhood_csv": paths.robinhood_csv,
        "empower_downloads": paths.downloads,
    }
    return build_investment_sources(raw_sources_cfg, paths.db_path)


def _full_build(
    paths: BuildPaths,
    config: RawConfig,
    start: date,
    end: date,
    *,
    no_validate: bool = False,
    dry_run_market: bool = False,
) -> list[AllocationRow]:
    print("\n[5] Computing full allocation...")
    investment_sources = _build_all_sources(paths, config)
    alloc = compute_daily_allocation(
        paths.db_path, DEFAULT_QJ_DB, config, start, end,
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
            paths, config, start, end,
            no_validate=no_validate, dry_run_market=dry_run_market,
        )

    inc_start = compute_inc_start(last, start, end)
    if inc_start > end:
        print(f"  Already up to date (last: {last})")
        return []

    print(f"\n[5] Computing allocation {inc_start} -> {end} (incremental)...")
    investment_sources = _build_all_sources(paths, config)
    alloc = compute_daily_allocation(
        paths.db_path, DEFAULT_QJ_DB, config, inc_start, end,
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
    _ingest_and_fetch(
        paths, config, end, prices_from_csv=args.prices_from_csv,
    )

    # Derive date range from ingested fidelity transactions
    start = _derive_start_date(paths, fallback=end)
    print(f"  Range: {start} -> {end}")

    _build_refresh_window(
        paths, config, start, end,
        no_validate=args.no_validate,
        dry_run_market=args.dry_run_market,
    )

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
