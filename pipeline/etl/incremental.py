"""Incremental build helpers for computed_daily."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from .db import get_connection

# ── Queries ─────────────────────────────────────────────────────────────────


def get_last_computed_date(db_path: Path) -> date | None:
    """Return the latest date in computed_daily, or None if empty."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT MAX(date) FROM computed_daily").fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None
    finally:
        conn.close()


def append_daily(db_path: Path, rows: list[dict[str, object]]) -> int:
    """Append new rows to computed_daily + computed_daily_tickers.

    Skips dates already in the DB.  Returns number of rows inserted.
    """
    if not rows:
        return 0

    conn = get_connection(db_path)
    try:
        existing = {r[0] for r in conn.execute("SELECT date FROM computed_daily").fetchall()}
        added = 0
        for r in rows:
            if r["date"] in existing:
                continue
            conn.execute(
                "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net, liabilities)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (r["date"], r["total"], r["us_equity"], r["non_us_equity"],
                 r["crypto"], r["safe_net"], r.get("liabilities", 0)),
            )
            tickers: list[dict[str, object]] = r.get("tickers") or []  # type: ignore[assignment]
            for t in tickers:
                conn.execute(
                    "INSERT OR REPLACE INTO computed_daily_tickers"
                    " (date, ticker, value, category, subtype, cost_basis, gain_loss, gain_loss_pct)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (r["date"], t["ticker"], t["value"], t["category"], t["subtype"],
                     t["cost_basis"], t["gain_loss"], t["gain_loss_pct"]),
                )
            added += 1
        conn.commit()
        return added
    finally:
        conn.close()
