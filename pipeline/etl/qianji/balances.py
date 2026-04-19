"""Qianji point-in-time balance replay.

Reverse-replay over the live ``user_asset`` snapshot: start from current
balances (which reflect *now*) and undo every bill with ``time >
end_of_day(as_of)``. Output balances are per-account in the account's
native currency — no FX conversion here. Callers (``etl.allocation``)
do the USD conversion at render time against a per-day CNY rate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..db import get_readonly_connection
from .config import _USER_TZ
from .currency import parse_qj_target_amount
from .ingest import _load_balances

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class QianjiSnapshot:
    """Qianji account state at some as_of date.

    ``balances`` is per-account balance in each account's native currency;
    ``currencies`` maps account name to ISO currency code (e.g. ``USD`` /
    ``CNY``). Currencies are snapshot-time independent — they're read from
    ``user_asset.currency`` alongside balances in a single SELECT so
    consumers don't need a second query.
    """
    balances: dict[str, float] = field(default_factory=dict)
    currencies: dict[str, str] = field(default_factory=dict)


def qianji_balances_at(db_path: Path, as_of: date | None = None) -> QianjiSnapshot:
    """Return Qianji account balances + currencies at ``as_of`` via reverse replay.

    Strategy: read live balances from ``user_asset`` (which reflects *now*, i.e.
    after every bill Qianji has ever recorded), then walk forward-in-time
    through every bill with ``time > end_of_day(as_of)`` and *undo* its
    effect on the live balances. Output balances are in each account's
    native currency — no FX conversion to USD at replay time.

    Bill-type dispatch table
    ------------------------
    Qianji encodes four bill kinds in ``user_bill.type`` (see
    :data:`_TYPE_MAP` for the internal-name mapping). For each, this
    function applies the **inverse** of the forward effect:

    =====  =========  ============================  ============================
    type   name       forward effect (on record)    reverse effect (here)
    =====  =========  ============================  ============================
    0      expense    ``balances[fromact] -= m``    ``balances[fromact] += m``
    1      income     ``balances[fromact] += m``    ``balances[fromact] -= m``
    2      transfer   ``balances[fromact] -= m``    ``balances[fromact] += m``
                      ``balances[targetact] += tv`` ``balances[targetact] -= tv``
    3      repayment  same as transfer              same as transfer
    =====  =========  ============================  ============================

    (``m`` is shorthand for ``money``, ``tv`` for target-value — see
    :func:`parse_qj_target_amount`.) Repayment is Qianji's model for "pay
    the credit card off" — a transfer from Checking to Credit Card with
    the same two-sided arithmetic as type 2.

    Notes on the forward model:

    - ``money`` is always expressed in the **source** account's native
      currency. For a USD Checking account, ``money`` is USD; for a CNY
      Alipay account, ``money`` is CNY. No cross-account normalization.
    - For type 2/3 transfers, ``targetact`` does NOT receive ``money``
      directly — it receives ``tv`` (target-value), which equals ``money``
      for same-currency transfers but differs for cross-currency ones.
    - Other type codes (seen occasionally in the wild: 4/5 collapsed
      categories) are silently skipped — they're either unsupported
      features or Qianji-internal bookkeeping and should not move balances.

    Cross-currency transfer — worked example
    ----------------------------------------
    User transfers 1000 USD from ``USD Account`` to ``CNY Account`` at an
    exchange rate of 7 CNY/USD. The bill is stored as:

    - ``type = 2``, ``money = 1000.0`` (source amount, USD)
    - ``fromact = "USD Account"``, ``targetact = "CNY Account"``
    - ``extra.curr = {"ss": "USD", "ts": "CNY", "tv": 7000.0}``

    Forward effect: ``USD Account -= 1000``, ``CNY Account += 7000``.

    If ``as_of`` is before the transfer date, this function calls
    :func:`parse_qj_target_amount` which returns ``tv = 7000.0`` (cross-
    currency branch: ``ss != ts`` and ``tv > 0``). Then:

    - ``balances["USD Account"] += 1000.0`` — undo the source-side debit.
    - ``balances["CNY Account"] -= 7000.0`` — undo the target-side credit
      **in its own native currency**, NOT in USD. This is why currencies
      stay per-account; the caller (:mod:`etl.allocation`) does the USD
      conversion at render time using the CNY rate appropriate for the
      replay date.

    Same-currency transfers fall through the ``ss == ts`` check in
    :func:`parse_qj_target_amount`, so ``tv == money`` and both sides move
    by the same magnitude — mathematically identical to "money out, money in".

    Timezone correctness (the 39% bug)
    ----------------------------------
    Qianji stores ``user_bill.time`` as a Unix epoch captured at save, so
    each bill lands at whatever UTC instant corresponds to the user's
    wall-clock tap. Truncating to a day in UTC mis-attributes every
    late-evening bill to the next UTC calendar day.

    For a user on US West Coast time, anything logged after ~16:00 local
    crosses midnight UTC and lands on the wrong day. The real-data audit
    measured this at **780 / 1994 bills (39%)** — every evening transaction
    systematically off-by-one. See commit 70373ae (2026-04-15,
    ``fix(data): 3 Qianji/pricing correctness bugs``) for the
    before/after validation.

    The cutoff here is constructed as ``datetime(year, month, day,
    23, 59, 59, tzinfo=_USER_TZ).timestamp()`` — end-of-day in the user's
    local zone, converted to a Unix epoch. Bills with ``time > cutoff``
    are reversed. A bill tapped at noon local on ``as_of`` stays (tapped
    *before* cutoff); a bill tapped at 00:01 the next local day gets
    reversed, matching the user's intuitive "what did I have at the end
    of this day?" mental model.

    :data:`_USER_TZ` defaults to ``America/Los_Angeles`` and is overridable
    via ``QIANJI_USER_TZ`` for tests and fixtures — the L2 regression
    fixture pins it to UTC to keep the golden deterministic.

    Inputs / outputs
    ----------------
    - ``db_path`` — path to the user's live Qianji SQLite DB (see
      :data:`DEFAULT_DB_PATH`). Missing file → empty :class:`QianjiSnapshot`
      and a WARNING log line (Qianji ingest is optional in the pipeline).
    - ``as_of`` — ``None`` returns *current* balances with no replay;
      otherwise reverse bills after end-of-local-day.
    - Returns :class:`QianjiSnapshot` with ``balances`` (per-account, native
      currency) and ``currencies`` (per-account ISO code) — both captured
      from the same ``user_asset`` SELECT for consistency.

    See also
    --------
    - :func:`parse_qj_target_amount` — cross-currency target-value extractor.
    - :func:`etl.qianji.parse_qj_amount` — USD conversion used during ingest
      (not here; replay keeps native currency).
    - :func:`_load_balances` — the live-balance reader this function seeds from.
    """
    if not db_path.exists():
        log.warning("Qianji DB not found: %s", db_path)
        return QianjiSnapshot()

    conn = get_readonly_connection(db_path)
    try:
        raw = _load_balances(conn)
        balances = {name: money for name, (money, _) in raw.items()}
        currencies = {name: curr for name, (_, curr) in raw.items()}

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
            else:
                # Unknown bill kind (e.g. type 4/5 collapsed categories or a
                # future Qianji feature). Don't raise — an unexpected bill must
                # not break reverse-replay for every other account. Just surface.
                log.warning(
                    "Qianji bill_type=%d unhandled (bill skipped)", bill_type,
                )

        return QianjiSnapshot(balances=balances, currencies=currencies)
    finally:
        conn.close()
