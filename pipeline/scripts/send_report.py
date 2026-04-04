"""Generate report JSON from local data files.

Data download/upload handled by GitHub Actions workflow via wrangler CLI.
This script only does report generation → JSON output.

Usage:
    python scripts/send_report.py --data-dir ./data
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path so we can import generate_asset_snapshot
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _build_report(data_dir: Path):  # noqa: ANN202
    """Build ReportData from files in data_dir."""
    from generate_asset_snapshot.config import load_config, manual_values_from_snapshot
    from generate_asset_snapshot.history import build_chart_data
    from generate_asset_snapshot.ingest.fidelity_history import load_transactions
    from generate_asset_snapshot.ingest.qianji_db import load_all_from_db
    from generate_asset_snapshot.portfolio import load_portfolio
    from generate_asset_snapshot.report import build_report
    from generate_asset_snapshot.types import DEFAULT_CNY_RATE, ReportSources

    config_path = data_dir / "config.json"
    if not config_path.exists():
        config_path = Path(__file__).resolve().parent.parent / "config.json"
    config = load_config(config_path)

    positions_csv = data_dir / "positions.csv"
    if not positions_csv.exists():
        raise SystemExit("No positions.csv found in data dir")

    cashflow = None
    balance_snapshot = None
    db_path = data_dir / "qianjiapp.db"
    if db_path.exists():
        cashflow, balance_snapshot = load_all_from_db(db_path)
        if cashflow and balance_snapshot:
            config["manual"] = manual_values_from_snapshot(balance_snapshot, config)
            print(f"  Qianji: {len(cashflow)} records", file=sys.stderr)

    portfolio = load_portfolio(positions_csv, config)

    transactions = None
    history_csv = data_dir / "history.csv"
    if history_csv.exists():
        transactions = load_transactions(history_csv)

    chart_data = build_chart_data(data_dir, cashflow=cashflow, config=config, portfolio_total=portfolio["total"])

    market_data = None
    try:
        from generate_asset_snapshot.market.yahoo import build_market_data

        cny_rate = balance_snapshot.get("cny_rate", DEFAULT_CNY_RATE) if balance_snapshot else DEFAULT_CNY_RATE
        market_data = build_market_data(cny_rate)
    except Exception:  # noqa: BLE001
        pass

    return build_report(
        portfolio,
        config,
        positions_csv.name,
        transactions=transactions,
        cashflow=cashflow,
        balance_snapshot=balance_snapshot,
        sources=ReportSources(market=market_data),
        chart_data=chart_data,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate report JSON")
    parser.add_argument("--data-dir", type=Path, required=True, help="Directory with positions.csv, history.csv, etc.")
    args = parser.parse_args()

    print("Generating report...", file=sys.stderr)
    report = _build_report(args.data_dir)

    # ── Net worth history: load, append, inject into report ──────────────
    import json as _json

    from generate_asset_snapshot.types import ChartData, SnapshotPoint

    history_path = args.data_dir / "net_worth_history.json"
    nw_history: list[dict[str, object]] = _json.loads(history_path.read_text()) if history_path.exists() else []

    # Append current net worth (deduplicate by month)
    current_nw = report.balance_sheet.net_worth if report.balance_sheet else report.total
    today = datetime.now().strftime("%Y-%m-01")
    existing_dates = {entry["date"] for entry in nw_history}
    if today not in existing_dates:
        nw_history.append({"date": today, "total": round(current_nw)})
        nw_history.sort(key=lambda x: str(x["date"]))
        print(f"  Net worth history: appended {today} = ${current_nw:,.0f}", file=sys.stderr)
    else:
        # Update current month's value
        for entry in nw_history:
            if entry["date"] == today:
                entry["total"] = round(current_nw)
        print(f"  Net worth history: updated {today} = ${current_nw:,.0f}", file=sys.stderr)

    # Write updated history back (workflow uploads to R2)
    history_path.write_text(_json.dumps(nw_history, indent=2))

    # Inject into report's chart_data
    trend = [SnapshotPoint(date=str(e["date"]), total=float(e["total"])) for e in nw_history]  # type: ignore[arg-type]
    if report.chart_data:
        report.chart_data.net_worth_trend = trend
    else:
        report.chart_data = ChartData(net_worth_trend=trend, monthly_flows=[])

    # ── Metadata ─────────────────────────────────────────────────────────
    sync_meta_path = args.data_dir / "sync_meta.json"
    sync_meta = _json.loads(sync_meta_path.read_text()) if sync_meta_path.exists() else {}

    metadata = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "positions_date": sync_meta.get("positions_date", ""),
        "history_date": sync_meta.get("history_date", ""),
        "qianji_date": sync_meta.get("qianji_date", ""),
    }

    from generate_asset_snapshot.renderers import json_renderer

    json_output = json_renderer.render(report, metadata=metadata)
    json_path = args.data_dir / "report.json"
    json_path.write_text(json_output)
    print(f"  JSON: {len(json_output)} chars -> {json_path}", file=sys.stderr)
    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
