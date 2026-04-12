"""Incremental build and cross-check verification for computed_daily."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .db import get_connection

# ── Types ───────────────────────────────────────────────────────────────────


@dataclass
class DailyDrift:
    date: str
    field: str
    persisted: float
    recomputed: float
    delta: float


# ── Queries ─────────────────────────────────────────────────────────────────

_COMPARE_FIELDS = ("total", "us_equity", "non_us_equity", "crypto", "safe_net")


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


def verify_daily(
    db_path: Path,
    recomputed: list[dict[str, object]],
    threshold: float = 1.0,
) -> list[DailyDrift]:
    """Compare recomputed allocation with persisted values.

    Returns drifts where abs(persisted - recomputed) > threshold.
    Dates in recomputed but not in DB are silently skipped.
    """
    conn = get_connection(db_path)
    try:
        persisted: dict[str, dict[str, float]] = {}
        for row in conn.execute(
            "SELECT date, total, us_equity, non_us_equity, crypto, safe_net FROM computed_daily"
        ):
            persisted[row[0]] = dict(zip(_COMPARE_FIELDS, row[1:], strict=True))
    finally:
        conn.close()

    drifts: list[DailyDrift] = []
    for r in recomputed:
        d = str(r["date"])
        old = persisted.get(d)
        if old is None:
            continue
        for field in _COMPARE_FIELDS:
            old_val = old[field]
            new_val = float(r[field])  # type: ignore[arg-type]
            delta = abs(new_val - old_val)
            if delta > threshold:
                drifts.append(DailyDrift(d, field, old_val, new_val, round(new_val - old_val, 2)))

    return drifts
