"""Nightly projection of computed_daily forward from the last authoritative date.

Runs on GitHub Actions right after the price sync. Pulls the last
local-authored date from ``sync_meta.last_date``, seeds from that date's
``computed_daily_tickers`` snapshot, and walks forward day-by-day through
``today`` using whatever prices D1 currently holds (freshly populated by
``sync_prices_nightly.py`` earlier in the same job). Writes the projected
rows with DELETE-then-INSERT so re-runs supersede earlier projections.

Only prices move — new transactions are invisible to this script. Local
sync's range-replace (see ``sync_to_d1.py``) overwrites these projections
with authoritative values on the next local run.

Requires ``CLOUDFLARE_API_TOKEN`` + ``CLOUDFLARE_ACCOUNT_ID`` (wrangler env).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

_PIPELINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PIPELINE))

from etl.projection import ProjectedDay, TickerRow, project_range  # noqa: E402

_WORKER_DIR = _PIPELINE.parent / "worker"
_D1_DATABASE = "portal-db"


# ── wrangler helpers (duplicated from sync_prices_nightly.py; small enough) ─


def _wrangler_query(sql: str) -> list[dict[str, Any]]:
    cmd = [
        "npx", "wrangler", "d1", "execute", _D1_DATABASE,
        "--remote", "--json", "--command", sql,
    ]
    result = subprocess.run(
        cmd, cwd=str(_WORKER_DIR), capture_output=True, text=True, check=True
    )
    idx = result.stdout.find("[")
    if idx < 0:
        raise RuntimeError(f"No JSON array in wrangler output:\n{result.stdout}")
    payload = json.loads(result.stdout[idx:])
    return payload[0].get("results", []) if payload else []


def _wrangler_exec_file(sql_path: Path) -> None:
    cmd = [
        "npx", "wrangler", "d1", "execute", _D1_DATABASE,
        "--remote", "--file", str(sql_path),
    ]
    subprocess.run(cmd, cwd=str(_WORKER_DIR), check=True)


def _escape(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


# ── D1 loaders ──────────────────────────────────────────────────────────────


def _last_authoritative_date() -> date | None:
    rows = _wrangler_query("SELECT value FROM sync_meta WHERE key = 'last_date'")
    if not rows:
        return None
    v = rows[0].get("value")
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _load_seed(seed_date: date) -> list[TickerRow]:
    rows = _wrangler_query(
        "SELECT ticker, value, category, subtype, cost_basis"
        f" FROM computed_daily_tickers WHERE date = '{seed_date.isoformat()}'"
    )
    return [
        TickerRow(
            ticker=r["ticker"],
            value=float(r.get("value") or 0),
            category=r.get("category") or "",
            subtype=r.get("subtype") or "",
            cost_basis=float(r.get("cost_basis") or 0),
        )
        for r in rows
    ]


def _load_prices_since(seed_date: date) -> dict[date, dict[str, float]]:
    """Build ffill'd {date: {symbol: price}} from daily_close since seed_date."""
    rows = _wrangler_query(
        "SELECT symbol, date, close FROM daily_close"
        f" WHERE date >= '{seed_date.isoformat()}' ORDER BY symbol, date"
    )
    by_sym: dict[str, list[tuple[date, float]]] = {}
    all_dates: set[date] = set()
    for r in rows:
        dd = date.fromisoformat(r["date"])
        by_sym.setdefault(r["symbol"], []).append((dd, float(r["close"])))
        all_dates.add(dd)
    result: dict[date, dict[str, float]] = {d: {} for d in all_dates}
    for sym, points in by_sym.items():
        points.sort()
        carry: float | None = None
        idx = 0
        for d in sorted(all_dates):
            while idx < len(points) and points[idx][0] <= d:
                carry = points[idx][1]
                idx += 1
            if carry is not None:
                result[d][sym] = carry
    return result


# ── Push ────────────────────────────────────────────────────────────────────


def _build_push_sql(last_auth: date, projected: list[ProjectedDay]) -> str:
    lines: list[str] = [
        # Supersede any prior CI projection (or stale beyond-last-authoritative rows)
        f"DELETE FROM computed_daily WHERE date > '{last_auth.isoformat()}';",
        f"DELETE FROM computed_daily_tickers WHERE date > '{last_auth.isoformat()}';",
    ]
    for p in projected:
        d_iso = p.date.isoformat()
        lines.append(
            "INSERT INTO computed_daily"
            " (date, total, us_equity, non_us_equity, crypto, safe_net, liabilities)"
            f" VALUES ({_escape(d_iso)}, {_escape(p.total)}, {_escape(p.us_equity)},"
            f" {_escape(p.non_us_equity)}, {_escape(p.crypto)}, {_escape(p.safe_net)},"
            f" {_escape(p.liabilities)});"
        )
        for t in p.tickers:
            lines.append(
                "INSERT INTO computed_daily_tickers"
                " (date, ticker, value, category, subtype, cost_basis, gain_loss, gain_loss_pct)"
                f" VALUES ({_escape(d_iso)}, {_escape(t['ticker'])}, {_escape(t['value'])},"
                f" {_escape(t['category'])}, {_escape(t['subtype'])}, {_escape(t['cost_basis'])},"
                f" {_escape(t['gain_loss'])}, {_escape(t['gain_loss_pct'])});"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    today = date.today()
    print("Step 1: finding last authoritative date from sync_meta...")
    last_auth = _last_authoritative_date()
    if last_auth is None:
        print("  sync_meta.last_date missing — run local sync first to seed. Exit.")
        return
    print(f"  last authoritative date: {last_auth}")

    if last_auth >= today:
        print(f"  already up-to-date (last_auth={last_auth} >= today={today}). Nothing to project.")
        return

    print(f"Step 2: loading seed tickers from computed_daily_tickers[{last_auth}]...")
    seed = _load_seed(last_auth)
    if not seed:
        print("  empty seed — cannot project. Exit.")
        return
    print(f"  seed tickers: {len(seed)}")

    print(f"Step 3: loading prices since {last_auth}...")
    prices = _load_prices_since(last_auth)
    print(f"  price-date entries: {len(prices)}")

    print(f"Step 4: projecting from {last_auth} → {today}...")
    projected = project_range(seed, last_auth, today, prices)
    print(f"  projected {len(projected)} weekdays: {[p.date.isoformat() for p in projected]}")

    if not projected:
        print("  nothing projected (weekend-only window). Exit.")
        return

    print("Step 5: pushing to D1 (DELETE beyond last_auth, INSERT projections)...")
    sql_text = _build_push_sql(last_auth, projected)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sql", delete=False, encoding="utf-8"
    ) as f:
        f.write(sql_text)
        sql_path = Path(f.name)
    try:
        _wrangler_exec_file(sql_path)
    finally:
        sql_path.unlink(missing_ok=True)
    print("Done.")


if __name__ == "__main__":
    main()
