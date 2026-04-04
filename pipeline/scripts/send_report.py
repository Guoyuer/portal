"""Generate report from local data, output HTML email + JSON for portal.

Data download/upload handled by GitHub Actions workflow via wrangler CLI.
This script only does report generation and email sending.

Requires env vars for email: GMAIL_ADDRESS, GMAIL_APP_PASSWORD

Usage:
    python scripts/send_report.py --data-dir ./data              # generate + send
    python scripts/send_report.py --data-dir ./data --dry-run    # generate only, print to stdout
"""

from __future__ import annotations

import argparse
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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

    # Config (from data dir or bundled)
    config_path = data_dir / "config.json"
    if not config_path.exists():
        config_path = Path(__file__).resolve().parent.parent / "config.json"
    config = load_config(config_path)

    # Positions (required)
    positions_csv = data_dir / "positions.csv"
    if not positions_csv.exists():
        raise SystemExit("No positions.csv found in data dir")

    # Qianji DB (optional)
    cashflow = None
    balance_snapshot = None
    db_path = data_dir / "qianjiapp.db"
    if db_path.exists():
        cashflow, balance_snapshot = load_all_from_db(db_path)
        if cashflow and balance_snapshot:
            config["manual"] = manual_values_from_snapshot(balance_snapshot, config)
            print(f"  Qianji: {len(cashflow)} records", file=sys.stderr)

    portfolio = load_portfolio(positions_csv, config)

    # History (optional)
    transactions = None
    history_csv = data_dir / "history.csv"
    if history_csv.exists():
        transactions = load_transactions(history_csv)

    # Chart data
    chart_data = build_chart_data(data_dir, cashflow=cashflow, config=config, portfolio_total=portfolio["total"])

    # Market data (optional)
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


def _send_email(subject: str, html_body: str) -> None:
    """Send HTML email via Gmail SMTP."""
    gmail_address = os.environ.get("GMAIL_ADDRESS", "")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    recipient = os.environ.get("RECIPIENT_EMAIL", gmail_address)

    if not gmail_address or not gmail_password:
        raise SystemExit("GMAIL_ADDRESS and GMAIL_APP_PASSWORD env vars required")

    msg = MIMEMultipart("alternative")
    msg["From"] = gmail_address
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_address, gmail_password)
        server.sendmail(gmail_address, recipient, msg.as_string())

    print(f"  Email sent to {recipient}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate report and send email")
    parser.add_argument("--data-dir", type=Path, required=True, help="Directory with positions.csv, history.csv, etc.")
    parser.add_argument("--dry-run", action="store_true", help="Generate report but don't send email")
    args = parser.parse_args()

    # Build report once, render to both formats
    print("Generating report...", file=sys.stderr)
    report = _build_report(args.data_dir)

    # JSON for portal
    from generate_asset_snapshot.renderers import json_renderer

    json_output = json_renderer.render(report)
    json_path = args.data_dir / "report.json"
    json_path.write_text(json_output)
    print(f"  JSON: {len(json_output)} chars -> {json_path}", file=sys.stderr)

    # HTML for email
    from generate_asset_snapshot.renderers import html

    html_body = html.render(report, email_safe=True)
    print(f"  HTML: {len(html_body)} chars", file=sys.stderr)

    from datetime import datetime

    subject = f"Asset Snapshot — {datetime.now().strftime('%B %d, %Y')}"

    if args.dry_run:
        print(html_body)
    else:
        print("Sending email...", file=sys.stderr)
        _send_email(subject, html_body)

    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
