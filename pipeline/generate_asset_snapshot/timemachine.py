"""Timemachine: replay Fidelity + Qianji to reconstruct portfolio at any date.

Verified against Portfolio_Positions_Apr-07-2026.csv:
  - 36/36 Fidelity positions exact match
  - 3/3 Fidelity cash balances exact match
  - Qianji balances: reverse-replay from current state, spot-checked

Usage:
  python -m generate_asset_snapshot.timemachine 2024-06-15
  python -m generate_asset_snapshot.timemachine 2024-06-15 --verify path/to/positions.csv
  python -m generate_asset_snapshot.timemachine --ingest path/to/new_export.csv
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Action prefixes that change share count (qty sign encodes direction)
POSITION_PREFIXES = (
    "YOU BOUGHT", "YOU SOLD", "REINVESTMENT", "REDEMPTION PAYOUT",
    "TRANSFERRED FROM", "TRANSFERRED TO", "DISTRIBUTION", "EXCHANGED TO",
)

# Money market funds ($1/share, treated as cash)
MM_SYMBOLS = frozenset({"SPAXX", "FZFXX", "FDRXX"})

STORE_HEADER = (
    "Run Date,Account,Account Number,Action,Symbol,Description,"
    "Type,Exchange Quantity,Exchange Currency,Currency,Price,Quantity,"
    "Exchange Rate,Commission,Fees,Accrued Interest,Amount,Settlement Date"
)

# Qianji default DB paths
_WIN_QJ_DB = Path(os.environ.get("APPDATA", "")) / "com.mutangtech.qianji.win/qianji_flutter/qianjiapp.db"
_MAC_QJ_DB = Path.home() / "Library/Containers/com.mutangtech.qianji.fltios/Data/Documents/qianjiapp.db"
DEFAULT_QJ_DB = _WIN_QJ_DB if sys.platform == "win32" else _MAC_QJ_DB


# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_date(mmddyyyy: str) -> date:
    return datetime.strptime(mmddyyyy.strip(), "%m/%d/%Y").date()


def _float(val: str) -> float:
    v = val.strip().replace(",", "").replace("$", "") if val else ""
    return float(v) if v else 0.0


def _load_raw_rows(path: Path) -> list[dict[str, str]]:
    """Load a Fidelity CSV (raw export or merged store) into row dicts."""
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    # Skip leading blanks
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    csv_text = "".join(lines[start:])
    rows = []
    for row in csv.DictReader(csv_text.splitlines()):
        run_date = (row.get("Run Date") or "").strip()
        if not run_date or not re.match(r"\d{2}/\d{2}/\d{4}", run_date):
            continue
        rows.append(row)
    return rows


# ── Replay engine ─────────────────────────────────────────────────────────────

def replay(store_path: Path, as_of: date | None = None) -> dict[str, Any]:
    """Replay transactions up to as_of, return positions and cash.

    Returns:
        {
            "positions": {(account, symbol): quantity, ...},
            "cash": {account: balance, ...},
            "as_of": date,
            "txn_count": int,
        }
    """
    rows = _load_raw_rows(store_path)

    holdings: dict[tuple[str, str], float] = defaultdict(float)
    cost_basis: dict[tuple[str, str], float] = defaultdict(float)
    cash_flow: dict[str, float] = defaultdict(float)
    mm_drip: dict[str, float] = defaultdict(float)
    count = 0

    for row in rows:
        txn_date = _parse_date(row["Run Date"])
        if as_of and txn_date > as_of:
            continue
        count += 1

        sym = (row.get("Symbol") or "").strip()
        acct = (row.get("Account Number") or "").strip()
        action = (row.get("Action") or "").upper()
        lot_type = (row.get("Type") or "").strip()
        qty = _float(row.get("Quantity", ""))
        amt = _float(row.get("Amount", ""))

        # ── Positions (exclude money market) ──
        if sym and sym not in MM_SYMBOLS and qty != 0 and any(action.startswith(p) for p in POSITION_PREFIXES):
            key = (acct, sym)
            # Cost basis: reduce proportionally on sell BEFORE updating holdings
            if action.startswith("YOU SOLD") and holdings[key] > 0:
                sold_fraction = min(abs(qty) / holdings[key], 1.0)
                cost_basis[key] -= cost_basis[key] * sold_fraction
            elif action.startswith(("YOU BOUGHT", "REINVESTMENT")):
                cost_basis[key] += abs(amt)
            holdings[key] += qty

        # ── Cash (exclude Type=Shares: stock distributions, lending, sweeps) ──
        if acct and lot_type != "Shares":
            cash_flow[acct] += amt
            if sym in MM_SYMBOLS and "REINVESTMENT" in action and qty != 0:
                mm_drip[acct] += qty

    positions = {k: round(v, 6) for k, v in holdings.items() if abs(v) > 0.001}
    cb_out = {k: round(v, 2) for k, v in cost_basis.items() if abs(v) > 0.01}
    cash = {acct: round(cash_flow[acct] + mm_drip.get(acct, 0.0), 2)
            for acct in cash_flow
            if re.match(r"^[A-Z0-9]+$", acct)}

    return {"positions": positions, "cost_basis": cb_out, "cash": cash, "as_of": as_of, "txn_count": count}


# ── Qianji replay ─────────────────────────────────────────────────────────────

def _qj_target_value(money: float, extra_str: str | None) -> float:
    """For cross-currency transfers, return the target-currency amount (tv).

    Qianji stores source amount in `money` (source currency).
    For cross-currency transfers, `extra.curr.tv` holds the target-currency amount.
    For same-currency, tv == money.
    """
    if not extra_str or extra_str == "null":
        return money
    try:
        extra = json.loads(extra_str)
    except (json.JSONDecodeError, TypeError):
        return money
    curr = extra.get("curr") if isinstance(extra, dict) else None
    if not isinstance(curr, dict):
        return money
    ss, ts, tv = curr.get("ss"), curr.get("ts"), curr.get("tv")
    if ss and ts and ss != ts and tv is not None and tv > 0:
        return float(tv)
    return money


def replay_qianji(db_path: Path, as_of: date | None = None) -> dict[str, float]:
    """Replay Qianji account balances at as_of date.

    Strategy: start from current balances (user_asset), reverse transactions
    after as_of. Each account balance stays in its native currency.

    Qianji conventions:
      - expense  (type 0): fromact loses money
      - income   (type 1): fromact gains money
      - transfer (type 2): fromact→targetact (cross-currency uses extra.curr.tv)
      - repayment(type 3): same as transfer
    """
    if not db_path.exists():
        log.warning("Qianji DB not found: %s", db_path)
        return {}

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        # Current balances and currencies
        balances: dict[str, float] = {}
        currencies: dict[str, str] = {}
        for name, money, currency in conn.execute(
            "SELECT name, money, currency FROM user_asset WHERE status = 0"
        ):
            balances[name] = float(money)
            currencies[name] = currency or "USD"

        if as_of is None:
            return balances

        # Reverse all transactions after end of as_of day
        cutoff = datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=UTC).timestamp()

        for bill_type, money, fromact, targetact, extra_str in conn.execute(
            "SELECT type, money, fromact, targetact, extra "
            "FROM user_bill WHERE status = 1 AND time > ? ORDER BY time",
            (cutoff,),
        ):
            money = float(money)
            fromact = fromact or ""
            targetact = targetact or ""
            tv = _qj_target_value(money, extra_str)

            if bill_type == 0:  # expense: fromact lost money → add back
                if fromact:
                    balances[fromact] = balances.get(fromact, 0) + money
            elif bill_type == 1:  # income: fromact gained money → subtract
                if fromact:
                    balances[fromact] = balances.get(fromact, 0) - money
            elif bill_type in (2, 3):  # transfer/repayment: reverse both sides
                if fromact:
                    balances[fromact] = balances.get(fromact, 0) + money
                if targetact:
                    balances[targetact] = balances.get(targetact, 0) - tv

        return balances
    finally:
        conn.close()


def replay_qianji_currencies(db_path: Path) -> dict[str, str]:
    """Return {account_name: currency} from Qianji DB."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {
            name: (currency or "USD")
            for name, currency in conn.execute("SELECT name, currency FROM user_asset WHERE status = 0")
        }
    finally:
        conn.close()


# ── Ingestion (merge new CSV into store, handling date overlap) ───────────────

def ingest(csv_path: Path, store_path: Path) -> int:
    """Merge a new Fidelity export CSV into the transaction store.

    For overlapping date ranges the new file replaces existing data.
    Returns total row count after merge.
    """
    new_rows = _load_raw_rows(csv_path)
    if not new_rows:
        log.warning("No transaction rows found in %s", csv_path)
        return 0

    new_dates = {_parse_date(r["Run Date"]) for r in new_rows}
    lo, hi = min(new_dates), max(new_dates)
    log.info("Ingesting %d rows from %s (%s → %s)", len(new_rows), csv_path.name, lo, hi)

    existing: list[dict[str, str]] = []
    if store_path.exists() and store_path.stat().st_size > 0:
        existing = _load_raw_rows(store_path)

    # Keep existing rows outside the new file's date range
    kept = [r for r in existing
            if _parse_date(r["Run Date"]) < lo or _parse_date(r["Run Date"]) > hi]

    merged = kept + new_rows
    merged.sort(key=lambda r: _parse_date(r["Run Date"]))

    _write_store(merged, store_path)
    log.info("Store now has %d rows (%s)", len(merged), store_path)
    return len(merged)


def _write_store(rows: list[dict[str, str]], path: Path) -> None:
    """Write rows back to CSV in Fidelity format."""
    fields = STORE_HEADER.split(",")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ── Verification ──────────────────────────────────────────────────────────────

def verify(store_path: Path, positions_csv: Path) -> None:
    """Verify replay results against a Fidelity positions snapshot."""
    # Load expected positions
    expected_pos: dict[tuple[str, str], float] = defaultdict(float)
    expected_cash: dict[str, float] = {}
    with open(positions_csv, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sym = (row.get("Symbol") or "").strip()
            acct = (row.get("Account Number") or "").strip()
            qty_str = (row.get("Quantity") or "").strip()
            val_str = (row.get("Current Value") or "").strip()
            if not sym or not acct:
                continue
            if "**" in sym:
                # Money market → cash
                expected_cash[acct] = _float(val_str)
                continue
            if not re.match(r"^[A-Z0-9]+$", acct):
                continue
            if qty_str:
                expected_pos[(acct, sym)] += float(qty_str)

    expected_pos = {k: v for k, v in expected_pos.items() if abs(v) > 0.001}

    # Replay
    result = replay(store_path)
    computed_pos = result["positions"]
    computed_cash = result["cash"]

    # Compare positions
    all_keys = sorted(set(expected_pos) | set(computed_pos))
    ok = mismatch = 0
    print(f"\n{'Account':<15} {'Symbol':<8} {'Expected':>12} {'Computed':>12} {'Diff':>10} {'Status'}")
    print("-" * 72)
    for key in all_keys:
        exp = expected_pos.get(key, 0.0)
        comp = computed_pos.get(key, 0.0)
        diff = comp - exp
        if abs(diff) < 0.01:
            ok += 1
        else:
            mismatch += 1
            status = "EXTRA" if key not in expected_pos else "MISSING" if key not in computed_pos else "MISMATCH"
            print(f"{key[0]:<15} {key[1]:<8} {exp:>12.3f} {comp:>12.3f} {diff:>+10.3f} {status}")
    print("-" * 72)
    print(f"Positions: {ok} OK, {mismatch} issues")

    # Compare cash
    print(f"\n{'Account':<15} {'Expected':>12} {'Computed':>12} {'Diff':>10}")
    print("-" * 55)
    cash_ok = 0
    for acct in sorted(set(expected_cash) | set(computed_cash)):
        exp = expected_cash.get(acct, 0.0)
        comp = computed_cash.get(acct, 0.0)
        diff = comp - exp
        tag = "OK" if abs(diff) < 0.01 else "MISMATCH"
        if abs(diff) < 0.01:
            cash_ok += 1
        print(f"{acct:<15} {exp:>12.2f} {comp:>12.2f} {diff:>+10.2f}  {tag}")
    print("-" * 55)
    print(f"Cash: {cash_ok}/{len(expected_cash)} OK")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    p = argparse.ArgumentParser(description="Timemachine: replay Fidelity + Qianji")
    p.add_argument("date", nargs="?", help="Replay as of YYYY-MM-DD (omit for all time)")
    p.add_argument("--store", default="pipeline/data/fidelity_transactions.csv",
                    help="Path to Fidelity transaction store CSV")
    p.add_argument("--qianji-db", default=str(DEFAULT_QJ_DB), help="Path to Qianji SQLite DB")
    p.add_argument("--ingest", metavar="CSV", help="Ingest a new Fidelity export CSV")
    p.add_argument("--verify", metavar="CSV", help="Verify against a positions snapshot CSV")
    args = p.parse_args()

    store = Path(args.store)
    qj_db = Path(args.qianji_db)

    if args.ingest:
        ingest(Path(args.ingest), store)
        return

    if args.verify:
        verify(store, Path(args.verify))
        return

    as_of = date.fromisoformat(args.date) if args.date else None
    result = replay(store, as_of)

    print(f"As of: {result['as_of'] or 'all time'}  ({result['txn_count']} Fidelity transactions)")
    print(f"\nFidelity positions ({len(result['positions'])} holdings):")
    for (acct, sym), qty in sorted(result["positions"].items()):
        print(f"  {acct}  {sym:<8} {qty:>12.3f}")
    print("\nFidelity cash:")
    for acct, bal in sorted(result["cash"].items()):
        print(f"  {acct}  ${bal:>12.2f}")

    # Qianji
    qj_balances = replay_qianji(qj_db, as_of)
    if qj_balances:
        currencies = replay_qianji_currencies(qj_db)
        print(f"\nQianji accounts ({len([b for b in qj_balances.values() if abs(b) >= 0.01])} with balance):")
        for acct, bal in sorted(qj_balances.items(), key=lambda x: -abs(x[1])):
            if abs(bal) >= 0.01:
                curr = currencies.get(acct, "USD")
                sym = "¥" if curr == "CNY" else "$"
                print(f"  {acct:<25} {sym}{bal:>12.2f}")


if __name__ == "__main__":
    main()
