"""Generate report JSON from local data files.

Data download/upload handled by GitHub Actions workflow via wrangler CLI.
This script only does report generation → JSON output.

Usage:
    python scripts/send_report.py --data-dir ./data
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path so we can import generate_asset_snapshot
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _log(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr)


def _build_report(data_dir: Path):  # noqa: ANN202
    """Build ReportData from files in data_dir."""
    from generate_asset_snapshot.config import load_config, manual_values_from_snapshot
    from generate_asset_snapshot.history import build_chart_data
    from generate_asset_snapshot.ingest.fidelity_history import load_transactions
    from generate_asset_snapshot.ingest.qianji_db import load_all_from_db
    from generate_asset_snapshot.portfolio import load_portfolio
    from generate_asset_snapshot.report import build_report
    from generate_asset_snapshot.types import ReportSources

    # ── Config ───────────────────────────────────────────────────────────
    config_path = data_dir / "config.json"
    if not config_path.exists():
        config_path = Path(__file__).resolve().parent.parent / "config.json"
    config = load_config(config_path)
    _log(f"Config: {config_path} ({len(config['assets'])} assets, goal=${config['goal']:,.0f})")

    # ── Positions ────────────────────────────────────────────────────────
    positions_csv = data_dir / "positions.csv"
    if not positions_csv.exists():
        raise SystemExit("No positions.csv found in data dir")
    portfolio = load_portfolio(positions_csv, config)
    _log(f"Portfolio: ${portfolio['total']:,.2f} ({len(portfolio['totals'])} tickers, {sum(portfolio['counts'].values())} lots)")

    # ── Qianji ───────────────────────────────────────────────────────────
    cashflow = None
    balance_snapshot = None
    db_path = data_dir / "qianjiapp.db"
    if db_path.exists():
        cashflow, balance_snapshot = load_all_from_db(db_path)
        if cashflow and balance_snapshot:
            config["manual"] = manual_values_from_snapshot(balance_snapshot, config)
            n_accounts = len(balance_snapshot.get("balances", {}))
            _log(f"Qianji: {len(cashflow)} records, {n_accounts} accounts, snapshot date={balance_snapshot.get('date', '?')}")
        else:
            _log("Qianji: DB exists but no data loaded")
    else:
        _log("Qianji: no DB found")

    # ── Transactions ─────────────────────────────────────────────────────
    transactions = None
    history_csv = data_dir / "history.csv"
    if history_csv.exists():
        transactions = load_transactions(history_csv)
        _log(f"Transactions: {len(transactions)} records")
    else:
        _log("Transactions: no history.csv found")

    # ── Chart data ───────────────────────────────────────────────────────
    chart_data = build_chart_data(data_dir, cashflow=cashflow, config=config, portfolio_total=portfolio["total"])
    flows_count = len(chart_data.monthly_flows) if chart_data else 0
    _log(f"Chart data: {flows_count} monthly flow points")

    # ── Previous report (for reconciliation) ─────────────────────────────
    prev_totals: dict[str, float] | None = None
    prev_date = ""
    prev_report_path = data_dir / "previous_report.json"
    if prev_report_path.exists():
        try:
            prev = json.loads(prev_report_path.read_text())
            prev_totals = {}
            for cat in prev.get("equityCategories", []) + prev.get("nonEquityCategories", []):
                for sub in cat.get("subtypes", []):
                    for h in sub.get("holdings", []):
                        prev_totals[h["ticker"]] = h["value"]
                for h in cat.get("holdings", []):
                    prev_totals[h["ticker"]] = h["value"]
            prev_date = prev.get("date", "")
            prev_total = sum(prev_totals.values())
            _log(f"Previous snapshot: {len(prev_totals)} tickers, ${prev_total:,.0f} ({prev_date})")
        except Exception as e:  # noqa: BLE001
            _log(f"[warn] Failed to load previous report: {e}")
            prev_totals = None
    else:
        _log("Previous report: not found (first run — no reconciliation)")

    # ── CNY rate (must succeed — affects asset calculations) ─────────────
    from generate_asset_snapshot.market.yahoo import build_holdings_detail, build_market_data, fetch_cny_rate

    if balance_snapshot:
        cny_rate = balance_snapshot["cny_rate"]
        _log(f"CNY rate: {cny_rate:.4f} (from Qianji snapshot)")
    else:
        cny_rate = fetch_cny_rate()
        _log(f"CNY rate: {cny_rate:.4f} (from Yahoo Finance)")

    # ── Market data (optional) ───────────────────────────────────────────
    market_data = None
    holdings_detail = None
    try:
        t0 = time.time()
        market_data = build_market_data(cny_rate)
        if market_data:
            idx_names = [i.ticker for i in market_data.indices]
            _log(f"Market data: indices={idx_names} ({time.time() - t0:.1f}s)")
        else:
            _log(f"Market data: no index data returned ({time.time() - t0:.1f}s)")
    except Exception as e:  # noqa: BLE001
        _log(f"[warn] Market data fetch failed: {e}")

    try:
        t0 = time.time()
        holdings_detail = build_holdings_detail(portfolio)
        if holdings_detail:
            top = [s.ticker for s in holdings_detail.top_performers[:3]]
            bottom = [s.ticker for s in holdings_detail.bottom_performers[:3]]
            _log(f"Holdings: {len(holdings_detail.all_stocks)} stocks, top={top}, bottom={bottom} ({time.time() - t0:.1f}s)")
        else:
            _log(f"Holdings: no data returned ({time.time() - t0:.1f}s)")
    except Exception as e:  # noqa: BLE001
        _log(f"[warn] Holdings detail fetch failed: {e}")

    # ── Build report ─────────────────────────────────────────────────────
    report = build_report(
        portfolio,
        config,
        positions_csv.name,
        transactions=transactions,
        cashflow=cashflow,
        balance_snapshot=balance_snapshot,
        sources=ReportSources(market=market_data, holdings_detail=holdings_detail),
        chart_data=chart_data,
        prev_totals=prev_totals,
        prev_date=prev_date,
    )

    # Log what sections are populated
    sections = []
    if report.activity:
        sections.append(f"activity({report.activity.period_start}..{report.activity.period_end})")
    if report.cashflow:
        sections.append(f"cashflow({report.cashflow.period})")
    if report.balance_sheet:
        sections.append(f"balance(nw=${report.balance_sheet.net_worth:,.0f})")
    if report.reconciliation:
        sections.append(f"reconciliation(Δ${report.reconciliation.total_change:,.0f})")
    if report.annual_summary:
        sections.append(f"annual({report.annual_summary.year})")
    if report.market:
        sections.append("market")
    if report.holdings_detail:
        sections.append("holdings")
    _log(f"Report sections: {', '.join(sections) or 'core only'}")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate report JSON")
    parser.add_argument("--data-dir", type=Path, required=True, help="Directory with positions.csv, history.csv, etc.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="  %(name)s: %(message)s",
        stream=sys.stderr,
    )

    t_start = time.time()
    print("=" * 60, file=sys.stderr)
    print(f"Report generation started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", file=sys.stderr)
    print(f"Data dir: {args.data_dir.resolve()}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    report = _build_report(args.data_dir)

    # ── Net worth history: load, append, inject into report ──────────────

    from generate_asset_snapshot.types import ChartData, SnapshotPoint

    history_path = args.data_dir / "net_worth_history.json"
    nw_history: list[dict[str, object]] = json.loads(history_path.read_text()) if history_path.exists() else []

    # Append current net worth (deduplicate by month)
    current_nw = report.balance_sheet.net_worth if report.balance_sheet else report.total
    today = datetime.now().strftime("%Y-%m-01")
    existing_dates = {entry["date"] for entry in nw_history}
    if today not in existing_dates:
        nw_history.append({"date": today, "total": round(current_nw)})
        nw_history.sort(key=lambda x: str(x["date"]))
        _log(f"Net worth history: appended {today} = ${current_nw:,.0f} ({len(nw_history)} total points)")
    else:
        for entry in nw_history:
            if entry["date"] == today:
                entry["total"] = round(current_nw)
        _log(f"Net worth history: updated {today} = ${current_nw:,.0f} ({len(nw_history)} total points)")

    # Write updated history back (workflow uploads to R2)
    history_path.write_text(json.dumps(nw_history, indent=2))

    # Inject into report's chart_data
    trend = [SnapshotPoint(date=str(e["date"]), total=float(e["total"])) for e in nw_history]  # type: ignore[arg-type]
    if report.chart_data:
        report.chart_data.net_worth_trend = trend
    else:
        report.chart_data = ChartData(net_worth_trend=trend, monthly_flows=[])

    # ── Metadata ─────────────────────────────────────────────────────────
    sync_meta_path = args.data_dir / "sync_meta.json"
    sync_meta = json.loads(sync_meta_path.read_text()) if sync_meta_path.exists() else {}

    metadata = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "positions_date": sync_meta.get("positions_date", ""),
        "history_date": sync_meta.get("history_date", ""),
        "qianji_date": sync_meta.get("qianji_date", ""),
    }
    _log(f"Metadata: positions={metadata['positions_date'] or '?'}, history={metadata['history_date'] or '?'}, qianji={metadata['qianji_date'] or '?'}")

    from generate_asset_snapshot.renderers import json_renderer

    json_output = json_renderer.render(report, metadata=metadata)
    json_path = args.data_dir / "report.json"
    json_path.write_text(json_output)

    # ── Economic indicators (optional — requires FRED_API_KEY) ──────────
    import os

    from generate_asset_snapshot.market.fred import fetch_fred_data

    fred_key = os.environ.get("FRED_API_KEY", "")
    econ_data = fetch_fred_data(fred_key)
    if econ_data:
        if market_data:
            econ_data["snapshot"].setdefault("usdCny", market_data.usd_cny)
        econ_data["generatedAt"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        econ_json = json.dumps(econ_data, indent=2)
        econ_path = args.data_dir / "econ.json"
        econ_path.write_text(econ_json)
        _log(f"Econ data: {len(econ_data['snapshot'])} indicators → {econ_path}")
    else:
        _log("Econ data: skipped (FRED API key not set or all series failed)")

    elapsed = time.time() - t_start
    print("=" * 60, file=sys.stderr)
    print(f"Done in {elapsed:.1f}s — {len(json_output):,} chars → {json_path}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)


if __name__ == "__main__":
    main()
