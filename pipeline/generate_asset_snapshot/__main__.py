"""CLI entry point: python -m generate_asset_snapshot."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from .config import load_config, manual_values_from_snapshot
from .ingest.fidelity_history import load_transactions
from .ingest.qianji_db import DEFAULT_DB_PATH, load_all_from_db
from .portfolio import load_portfolio
from .report import build_report
from .types import DEFAULT_CNY_RATE, ConfigError, PortfolioError

_DOWNLOADS_DIR = Path.home() / "Downloads"


def _find_latest(directory: Path, prefix: str) -> Path | None:
    """Find the most recently modified file matching *prefix* in *directory*."""
    matches = sorted(directory.glob(f"{prefix}*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _resolve_csv(explicit: Path | None, prefix: str, label: str) -> Path:
    """Return *explicit* if given, otherwise auto-detect from Downloads."""
    if explicit is not None:
        if not explicit.exists():
            raise SystemExit(f"{label} not found: {explicit}")
        return explicit
    found = _find_latest(_DOWNLOADS_DIR, prefix)
    if found is None:
        raise SystemExit(f"No {label} found in {_DOWNLOADS_DIR} (glob: {prefix}*)")
    print(f"Auto-detected {label}: {found.name}", file=sys.stderr)
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description="Portfolio snapshot generator")
    parser.add_argument(
        "csv",
        nargs="?",
        type=Path,
        default=None,
        help="Fidelity Portfolio Positions CSV (auto-detected from ~/Downloads if omitted)",
    )
    parser.add_argument(
        "history",
        nargs="?",
        type=Path,
        default=None,
        help="Fidelity Accounts History CSV (auto-detected from ~/Downloads if omitted)",
    )
    parser.add_argument("--config", type=Path, required=True, help="JSON config file")
    parser.add_argument("--contribute", type=float, default=0, help="Contribution amount to simulate")
    parser.add_argument("--hide", action="store_true", help="Hide dollar values (show percentages only)")
    parser.add_argument("--month", type=str, default="", help="Reporting month YYYY-MM (default: latest complete)")
    parser.add_argument("--qianji-db", type=Path, default=DEFAULT_DB_PATH, help="Qianji SQLite database path")
    parser.add_argument(
        "--format",
        choices=["html", "json"],
        default="html",
        help="Output format (default: html)",
    )
    args = parser.parse_args()

    csv_path = _resolve_csv(args.csv, "Portfolio_Positions", "positions CSV")
    history_path = _resolve_csv(args.history, "Accounts_History", "history CSV")

    try:
        config = load_config(args.config)
    except ConfigError as e:
        raise SystemExit(str(e)) from e

    # Load Qianji data directly from SQLite (auto-detected, no export needed)
    cashflow = None
    balance_snapshot = None
    if args.qianji_db.exists():
        cashflow, balance_snapshot = load_all_from_db(args.qianji_db)
        if cashflow and balance_snapshot:
            config["manual"] = manual_values_from_snapshot(balance_snapshot, config)
            print(f"Qianji: {len(cashflow)} records from {args.qianji_db.name}", file=sys.stderr)
    else:
        print(f"Qianji DB not found: {args.qianji_db}", file=sys.stderr)

    try:
        portfolio = load_portfolio(csv_path, config)
    except PortfolioError as e:
        raise SystemExit(str(e)) from e

    transactions = load_transactions(history_path)

    # Fetch market data (optional — API failure doesn't block report)
    market_data = None
    try:
        from .market.yahoo import build_market_data

        cny_rate = balance_snapshot.get("cny_rate", DEFAULT_CNY_RATE) if balance_snapshot else DEFAULT_CNY_RATE
        market_data = build_market_data(cny_rate)
    except Exception:  # noqa: BLE001
        pass  # yfinance not installed or API down

    from .history import build_chart_data
    from .types import ReportSources

    # Extract date from CSV filename for chart data alignment
    _date_match = re.search(r"Portfolio_Positions_([A-Za-z]+-\d+-\d+)", csv_path.name)
    _report_date = datetime.strptime(_date_match.group(1), "%b-%d-%Y").strftime("%Y-%m-%d") if _date_match else ""

    chart_data = build_chart_data(
        csv_path.parent,
        cashflow=cashflow,
        config=config,
        portfolio_total=portfolio["total"],
        report_date=_report_date,
    )

    report = build_report(
        portfolio,
        config,
        csv_path.name,
        contribute=args.contribute,
        transactions=transactions,
        cashflow=cashflow,
        balance_snapshot=balance_snapshot,
        report_month=args.month,
        sources=ReportSources(market=market_data),
        chart_data=chart_data,
    )

    if args.format == "json":
        from .renderers import json_renderer

        print(json_renderer.render(report))
    else:
        from .renderers import html

        print(html.render(report))


if __name__ == "__main__":
    main()
