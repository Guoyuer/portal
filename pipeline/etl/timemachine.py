"""Timemachine: Qianji balance reverse-replay + CLI.

The Fidelity replay engine lives in :mod:`etl.replay`
(:func:`replay_transactions`). This module now only hosts the Qianji-side
reverse-replay (``replay_qianji``) and the unified CLI that prints both
sides at a given ``as_of`` date.

Usage:
  python -m etl.timemachine 2024-06-15
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .ingest.qianji_db import _USER_TZ, DEFAULT_DB_PATH, parse_qj_target_amount
from .sources.fidelity import MM_SYMBOLS

#: Public re-export of the Qianji DB path. Module-level assignment makes mypy
#: --strict accept the re-export (which `... as ...` did not under strict).
DEFAULT_QJ_DB = DEFAULT_DB_PATH

log = logging.getLogger(__name__)


# ── Qianji replay ─────────────────────────────────────────────────────────────


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

        # Reverse all transactions after end of as_of day, anchored in the
        # user's wall-clock timezone. UTC cutoff would make "as_of=2026-04-15"
        # end at 4 PM PT that day, mis-reversing any late-evening activity.
        cutoff = datetime(
            as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=_USER_TZ,
        ).timestamp()

        for bill_type, money, fromact, targetact, extra_str in conn.execute(
            "SELECT type, money, fromact, targetact, extra "
            "FROM user_bill WHERE status = 1 AND time > ? ORDER BY time",
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


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    p = argparse.ArgumentParser(description="Timemachine: replay Fidelity + Qianji")
    p.add_argument("date", nargs="?", help="Replay as of YYYY-MM-DD (defaults to today)")
    p.add_argument(
        "--db",
        default="pipeline/data/timemachine.db",
        help="Path to timemachine SQLite DB (default: pipeline/data/timemachine.db)",
    )
    p.add_argument("--qianji-db", default=str(DEFAULT_QJ_DB), help="Path to Qianji SQLite DB")
    args = p.parse_args()

    # Deferred import sidesteps the ``etl.replay`` ↔ ``etl.sources`` cycle.
    from etl.replay import replay_transactions
    from etl.sources.fidelity import TABLE as _T

    db = Path(args.db)
    qj_db = Path(args.qianji_db)
    as_of = date.fromisoformat(args.date) if args.date else date.today()

    result = replay_transactions(
        db, _T, as_of,
        date_col="run_date", ticker_col="symbol", amount_col="amount",
        account_col="account_number",
        exclude_tickers=MM_SYMBOLS,
        track_cash=True, lot_type_col="lot_type",
        mm_drip_tickers=MM_SYMBOLS,
    )

    print(f"As of: {as_of}")
    print(f"\nFidelity positions ({len(result.positions)} holdings):")
    for (acct, sym), st in sorted(result.positions.items()):
        print(f"  {acct}  {sym:<8} {st.quantity:>12.3f}")
    print("\nFidelity cash:")
    for acct, bal in sorted(result.cash.items()):
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
