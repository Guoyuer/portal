"""Qianji point-in-time balance replay.

Reverse-replay over the live ``user_asset`` snapshot: start from current
balances (which reflect *now*) and undo every bill with ``time >
end_of_day(as_of)``. Output balances are per-account in the account's
native currency — no FX conversion here. Callers (``etl.allocation``)
do the USD conversion at render time against a per-day CNY rate.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path

from ..db import get_readonly_connection
from .config import _USER_TZ
from .currency import parse_qj_target_amount

log = logging.getLogger(__name__)
_BASE_CURRENCY = "USD"


def _load_balances(conn: sqlite3.Connection) -> dict[str, tuple[float, str]]:
    """Load active account balances and currencies from an open Qianji DB."""
    balances = {
        name: (float(money), currency or _BASE_CURRENCY)
        for name, money, currency in conn.execute("SELECT name, money, currency FROM user_asset WHERE status = 0")
    }
    log.info("Qianji balances: %d accounts", len(balances))
    return balances


def qianji_currencies(db_path: Path) -> dict[str, str]:
    """Return active Qianji account currency codes."""
    if not db_path.exists():
        log.warning("Qianji DB not found: %s", db_path)
        return {}
    conn = get_readonly_connection(db_path)
    try:
        return {name: curr for name, (_, curr) in _load_balances(conn).items()}
    finally:
        conn.close()


def qianji_balances_at(db_path: Path, as_of: date) -> dict[str, float]:
    """Return Qianji account balances at ``as_of`` via reverse replay.

    Qianji stores only the current account balances in ``user_asset``. To
    answer a historical date, start from that live snapshot and undo every
    bill after the user's local end-of-day cutoff. Balances stay in each
    account's native currency; ``etl.allocation`` applies FX later.

    Reverse effects mirror Qianji's forward model:
    - expense: add ``money`` back to ``fromact``
    - income: subtract ``money`` from ``fromact``
    - transfer/repayment: add ``money`` to ``fromact`` and subtract parsed
      target value from ``targetact``

    The cutoff uses ``_USER_TZ`` instead of UTC. This preserves late-evening
    transactions on their user-visible day, which was the source of a real
    off-by-one data bug.
    """
    if not db_path.exists():
        log.warning("Qianji DB not found: %s", db_path)
        return {}

    conn = get_readonly_connection(db_path)
    try:
        raw = _load_balances(conn)
        balances = {name: money for name, (money, _) in raw.items()}

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
            else:
                # Unknown bill kind (e.g. type 4/5 collapsed categories or a
                # future Qianji feature). Don't raise — an unexpected bill must
                # not break reverse-replay for every other account. Just surface.
                log.warning(
                    "Qianji bill_type=%d unhandled (bill skipped)", bill_type,
                )

        return balances
    finally:
        conn.close()
