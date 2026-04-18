"""Timemachine: Qianji balance reverse-replay.

Returns a :class:`QianjiSnapshot` — native-currency per-account balances
plus each account's currency — at a given ``as_of`` date (or "today" when
``as_of=None``).

Strategy: start from the current ``user_asset`` balances and walk bills
with ``time > as_of`` in forward order, undoing each bill's effect on
the source/target account. Cross-currency transfers use
``extra.curr.tv`` (see :mod:`etl.ingest.qianji_db`).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .ingest.qianji_db import _USER_TZ, parse_qj_target_amount

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class QianjiSnapshot:
    """Qianji account state at some as_of date.

    ``balances`` is the per-account balance in each account's native
    currency; ``currencies`` maps account name to ISO currency code (3-
    letter, e.g. ``USD`` / ``CNY``). Currencies are snapshot-time
    independent — they're read from ``user_asset.currency`` alongside
    balances and returned here so consumers don't need a second query.
    """
    balances: dict[str, float] = field(default_factory=dict)
    currencies: dict[str, str] = field(default_factory=dict)


def qianji_balances_at(db_path: Path, as_of: date | None = None) -> QianjiSnapshot:
    """Return Qianji account balances + currencies at ``as_of``.

    Starts from current balances (``user_asset``); when ``as_of`` is
    given, reverses every bill after end-of-day ``as_of`` (wall-clock in
    ``_USER_TZ``). Each account balance stays in its native currency.

    Qianji bill-type conventions:
      - expense  (type 0): fromact loses money
      - income   (type 1): fromact gains money
      - transfer (type 2): fromact→targetact (cross-currency uses
        ``extra.curr.tv``)
      - repayment(type 3): same as transfer

    When the Qianji DB doesn't exist, returns an empty snapshot.
    """
    if not db_path.exists():
        log.warning("Qianji DB not found: %s", db_path)
        return QianjiSnapshot()

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        balances: dict[str, float] = {}
        currencies: dict[str, str] = {}
        for name, money, currency in conn.execute(
            "SELECT name, money, currency FROM user_asset WHERE status = 0"
        ):
            balances[name] = float(money)
            currencies[name] = currency or "USD"

        if as_of is None:
            return QianjiSnapshot(balances=balances, currencies=currencies)

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

        return QianjiSnapshot(balances=balances, currencies=currencies)
    finally:
        conn.close()
