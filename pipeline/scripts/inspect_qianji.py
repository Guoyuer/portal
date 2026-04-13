"""Inspect Qianji balance replay: reverse-replay from current balances.

Debug/inspect tool — prints balances, no drift gate. See `verify_positions.py`
for the Fidelity gate automation uses.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import UTC, date, datetime
from pathlib import Path

# Ensure the pipeline package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl.ingest.qianji_db import parse_qj_target_amount

DB_PATH = Path(os.environ.get("APPDATA", "")) / "com.mutangtech.qianji.win/qianji_flutter/qianjiapp.db"


def replay_qianji(db_path: Path, as_of: date | None = None) -> dict[str, float]:
    """Replay Qianji balances at as_of date.

    Strategy: start from current balances, reverse transactions after as_of.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    # Current balances (native currency)
    balances: dict[str, float] = {}
    for name, money in conn.execute("SELECT name, money FROM user_asset WHERE status = 0"):
        balances[name] = float(money)

    if as_of is None:
        conn.close()
        return balances

    # Cutoff timestamp (end of as_of day, UTC)
    cutoff = datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=UTC).timestamp()

    # Reverse transactions after cutoff
    for bill_type, money, fromact, targetact, extra_str in conn.execute(
        "SELECT type, money, fromact, targetact, extra FROM user_bill WHERE status = 1 AND time > ? ORDER BY time",
        (cutoff,),
    ):
        money = float(money)
        fromact = fromact or ""
        targetact = targetact or ""
        tv = parse_qj_target_amount(money, extra_str)

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

    conn.close()
    return balances


def main() -> None:
    as_of = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None

    if as_of:
        print(f"Replaying balances as of {as_of}")
    else:
        print("Current balances (no replay)")

    balances = replay_qianji(DB_PATH, as_of)

    print(f"\n{'Account':<25} {'Balance':>12}")
    print("-" * 40)
    for acct, bal in sorted(balances.items(), key=lambda x: -abs(x[1])):
        if abs(bal) >= 0.01:
            print(f"{acct:<25} {bal:>12.2f}")


if __name__ == "__main__":
    main()
