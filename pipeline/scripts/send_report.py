"""Download data from GCS, generate report, send via Gmail SMTP.

Designed to run in GitHub Actions on a schedule. Requires env vars:
    GMAIL_ADDRESS, GMAIL_APP_PASSWORD, RECIPIENT_EMAIL (optional, defaults to GMAIL_ADDRESS)

Usage:
    python scripts/send_report.py                    # generate + send
    python scripts/send_report.py --dry-run           # generate only, print to stdout
    python scripts/send_report.py --data-dir /tmp/d   # use local files instead of GCS
"""

from __future__ import annotations

import argparse
import os
import smtplib
import sys
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# Add project root to path so we can import generate_asset_snapshot
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

GCS_BUCKET = "asset-snapshot-data"


def _download_from_gcs(dest: Path) -> None:
    """Download latest data files from GCS."""
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)

    for blob_name, local_name in [
        ("latest/positions.csv", "positions.csv"),
        ("latest/history.csv", "history.csv"),
        ("latest/qianjiapp.db", "qianjiapp.db"),
        ("latest/config.json", "config.json"),
    ]:
        blob = bucket.blob(blob_name)
        if blob.exists():
            blob.download_to_filename(str(dest / local_name))
            print(f"  Downloaded {blob_name}", file=sys.stderr)
        else:
            print(f"  [skip] {blob_name} not found", file=sys.stderr)


def _generate_report(data_dir: Path) -> str:
    """Generate HTML report from files in data_dir."""
    from generate_asset_snapshot.config import load_config, manual_values_from_snapshot
    from generate_asset_snapshot.history import build_chart_data
    from generate_asset_snapshot.ingest.fidelity_history import load_transactions
    from generate_asset_snapshot.ingest.qianji_db import load_all_from_db
    from generate_asset_snapshot.portfolio import load_portfolio
    from generate_asset_snapshot.renderers import html
    from generate_asset_snapshot.report import build_report
    from generate_asset_snapshot.types import DEFAULT_CNY_RATE, ReportSources

    # Config (from GCS or bundled)
    config_path = data_dir / "config.json"
    if not config_path.exists():
        config_path = Path(__file__).resolve().parent.parent / "config.json"
    config = load_config(config_path)

    # Positions (required)
    positions_csv = data_dir / "positions.csv"
    if not positions_csv.exists():
        raise SystemExit("No positions.csv found")

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

    # Chart data (monthly flows from Qianji; no historical CSVs on GCS)
    chart_data = build_chart_data(data_dir, cashflow=cashflow, config=config, portfolio_total=portfolio["total"])

    # Market data (optional)
    market_data = None
    try:
        from generate_asset_snapshot.market.yahoo import build_market_data

        cny_rate = balance_snapshot.get("cny_rate", DEFAULT_CNY_RATE) if balance_snapshot else DEFAULT_CNY_RATE
        market_data = build_market_data(cny_rate)
    except Exception:  # noqa: BLE001
        pass

    report = build_report(
        portfolio,
        config,
        positions_csv.name,
        transactions=transactions,
        cashflow=cashflow,
        balance_snapshot=balance_snapshot,
        sources=ReportSources(market=market_data),
        chart_data=chart_data,
    )

    return html.render(report, email_safe=True)


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
    parser.add_argument("--dry-run", action="store_true", help="Generate report but don't send email")
    parser.add_argument("--data-dir", type=Path, help="Use local data directory instead of GCS")
    args = parser.parse_args()

    # Get data
    if args.data_dir:
        data_dir = args.data_dir
        print(f"Using local data: {data_dir}", file=sys.stderr)
    else:
        data_dir = Path(tempfile.mkdtemp())
        print("Downloading from GCS...", file=sys.stderr)
        _download_from_gcs(data_dir)

    # Generate
    print("Generating report...", file=sys.stderr)
    html_body = _generate_report(data_dir)
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
