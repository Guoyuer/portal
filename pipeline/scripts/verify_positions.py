"""Verify: replay transactions -> compare computed share quantities vs positions snapshot.

Reads transactions from the timemachine.db SQLite (fidelity_transactions table) and
compares against a Fidelity Portfolio_Positions_*.csv snapshot. Exits non-zero on any
mismatch so the script is usable as an automation gate.

Usage:
    python scripts/verify_positions.py --positions ~/Downloads/Portfolio_Positions_Apr-07-2026.csv
    python scripts/verify_positions.py --positions <path> --tolerance 0.01
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parent.parent
_DB_PATH = Path(os.environ.get("PORTAL_DB_PATH", str(_PROJECT_DIR / "data" / "timemachine.db")))


# ── Parse positions snapshot ──────────────────────────────────────────────────
def load_positions(path: Path) -> dict[tuple[str, str], float]:
    """Return {(account, symbol): quantity} from positions CSV."""
    positions: dict[tuple[str, str], float] = defaultdict(float)
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sym = (row.get("Symbol") or "").strip()
            acct = (row.get("Account Number") or "").strip()
            qty_str = (row.get("Quantity") or "").strip()
            if not sym or not qty_str or "**" in sym:
                continue
            # Skip non-Fidelity brokerage accounts (401k, crypto, etc.)
            if not re.match(r"^[A-Z0-9]+$", acct):
                continue
            positions[(acct, sym)] += float(qty_str)
    return dict(positions)


# ── Replay transactions from timemachine.db ──────────────────────────────────
def replay_transactions(db_path: Path) -> dict[tuple[str, str], float]:
    """Replay all fidelity_transactions from SQLite, return {(account, symbol): quantity}."""
    holdings: dict[tuple[str, str], float] = defaultdict(float)
    # Action prefixes that affect share count (qty sign encodes direction)
    position_prefixes = (
        "YOU BOUGHT", "YOU SOLD", "REINVESTMENT", "REDEMPTION PAYOUT",
        "TRANSFERRED FROM", "TRANSFERRED TO", "DISTRIBUTION",
        "EXCHANGED TO",
    )
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT action, symbol, account_number, quantity FROM fidelity_transactions"
        ).fetchall()
    finally:
        conn.close()

    for action, sym, acct, qty in rows:
        sym = (sym or "").strip()
        acct = (acct or "").strip()
        if not sym or qty is None:
            continue
        qty_f = float(qty)
        if qty_f == 0:
            continue

        action_upper = (action or "").upper()
        if any(action_upper.startswith(p) for p in position_prefixes):
            holdings[(acct, sym)] += qty_f

    return {k: v for k, v in holdings.items() if abs(v) > 0.0001}


# ── Compare ──────────────────────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verify share counts: replay vs Portfolio_Positions snapshot")
    p.add_argument("--positions", type=Path, required=True,
                   help="Path to Fidelity Portfolio_Positions_*.csv snapshot (required)")
    p.add_argument("--tolerance", type=float, default=0.01,
                   help="Per-(account,symbol) share-count tolerance (default 0.01)")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    if not args.positions.exists():
        print(f"Error: positions CSV not found: {args.positions}", file=sys.stderr)
        return 1

    if not _DB_PATH.exists():
        print(f"Error: timemachine.db not found: {_DB_PATH}", file=sys.stderr)
        return 1

    expected = load_positions(args.positions)
    computed = replay_transactions(_DB_PATH)

    all_keys = sorted(set(expected) | set(computed))

    print(f"  Using positions CSV: {args.positions}")
    print(f"  Using timemachine.db: {_DB_PATH}")
    print(f"{'Account':<15} {'Symbol':<8} {'Expected':>12} {'Computed':>12} {'Diff':>10} {'Status'}")
    print("-" * 72)

    match = 0
    mismatch = 0
    missing = 0

    for key in all_keys:
        acct, sym = key
        exp = expected.get(key, 0)
        comp = computed.get(key, 0)
        diff = comp - exp

        if abs(diff) < args.tolerance:
            status = "OK"
            match += 1
        elif key not in expected:
            status = "EXTRA"
            mismatch += 1
        elif key not in computed:
            status = "MISSING"
            missing += 1
        else:
            status = "MISMATCH"
            mismatch += 1

        if status != "OK":
            print(f"{acct:<15} {sym:<8} {exp:>12.3f} {comp:>12.3f} {diff:>+10.3f} {status}")

    print("-" * 72)
    print(f"Match: {match}, Mismatch: {mismatch}, Missing: {missing}")

    # Exit non-zero on any drift so automation can gate on this.
    if mismatch + missing > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
