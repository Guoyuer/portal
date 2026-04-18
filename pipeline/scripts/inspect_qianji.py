"""Inspect Qianji balance replay: reverse-replay from current balances.

Debug/inspect tool — prints balances, no drift gate. See `verify_positions.py`
for the Fidelity gate automation uses.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# Ensure the pipeline package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl.ingest.qianji_db import DEFAULT_DB_PATH, qianji_balances_at


def main() -> None:
    as_of = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None

    if as_of:
        print(f"Replaying balances as of {as_of}")
    else:
        print("Current balances (no replay)")

    snapshot = qianji_balances_at(DEFAULT_DB_PATH, as_of)

    print(f"\n{'Account':<25} {'Balance':>12}")
    print("-" * 40)
    for acct, bal in sorted(snapshot.balances.items(), key=lambda x: -abs(x[1])):
        if abs(bal) >= 0.01:
            print(f"{acct:<25} {bal:>12.2f}")


if __name__ == "__main__":
    main()
