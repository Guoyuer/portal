"""Qianji currency conversion — ``extra.curr`` decoding and USD/CNY handling.

Qianji stores each bill's currency metadata in the ``extra`` JSON blob
(``ss``/``sv`` source, ``bs``/``bv`` base, ``ts``/``tv`` target). This
module centralises:

- Decoding the ``curr`` sub-dict.
- Picking the CNY rate for a bill's date (weekend walk-back).
- Resolving the base-currency (USD) amount, with the documented
  "ss != bs but bv == sv" quirk fallback.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from .config import _CONVERSION_TOLERANCE

log = logging.getLogger(__name__)


def _decode_curr(extra_str: str | None) -> dict[str, Any] | None:
    """Return ``extra.curr`` dict, or None if absent/malformed."""
    if not extra_str or extra_str == "null":
        return None
    try:
        extra = json.loads(extra_str)
    except (json.JSONDecodeError, TypeError):
        return None
    curr = extra.get("curr") if isinstance(extra, dict) else None
    return curr if isinstance(curr, dict) else None


def _resolve_cny_rate(
    bill_date: date | None,
    historical: Mapping[date, float] | None,
) -> float | None:
    """Pick the historical CNY rate for a bill date, with weekend walk-back."""
    if bill_date is not None and historical:
        for delta in range(8):
            d = bill_date - timedelta(days=delta)
            if d in historical:
                return historical[d]
    return None


def parse_qj_amount(
    money: float,
    extra_str: str | None,
    *,
    bill_date: date | None = None,
    historical_cny_rates: Mapping[date, float] | None = None,
) -> float:
    """Return the base-currency (USD) amount for a Qianji bill.

    Qianji's ``extra.curr`` encodes currency conversion metadata:
      - ``ss`` / ``sv`` — source currency + amount
      - ``bs`` / ``bv`` — base currency + amount (USD-denominated)
      - ``ts`` / ``tv`` — target currency + amount (transfers only)

    For cashflow aggregation we need USD, so return ``bv`` when the bill
    crossed currencies and ``bv != sv``.

    **Qianji data quirk:** Some bills have ``ss != bs`` (e.g. source CNY, base
    USD) but ``bv == sv`` — Qianji labelled the base as USD but the user
    never entered the conversion. When this happens:
      - If ``bill_date`` + ``historical_cny_rates`` are supplied, use the
        rate for the bill's date (walks back up to 7 days for weekends /
        holidays). **This is the primary path** — it makes the USD amount
        stable across runs, so reporting snapshots compare stable row identity
        and do not report FX-drift ghosts.
      - Else fail closed when a historical map was supplied but has no rate
        for the bill window; using today's live FX rate would rewrite history.
      - Else log a warning and fall back to ``money`` unchanged for direct
        parser calls that do not provide historical context.
    """
    curr = _decode_curr(extra_str)
    if curr is None:
        return float(money)
    ss, bs, bv, sv = curr.get("ss"), curr.get("bs"), curr.get("bv"), curr.get("sv")
    if ss and bs and ss != bs and bv is not None and sv is not None:
        if abs(bv - sv) > _CONVERSION_TOLERANCE:
            return float(bv)
        # Unconverted quirk: ss != bs but bv == sv.
        if ss == "CNY" and bs == "USD":
            rate = _resolve_cny_rate(bill_date, historical_cny_rates)
            if rate:
                log.warning(
                    "Qianji bill with unconverted CNY→USD label (bv=sv=%.2f); "
                    "converting source amount %.2f CNY → USD at rate %.4f",
                    sv, money, rate,
                )
                return float(money) / rate
            if bill_date is not None and historical_cny_rates is not None:
                msg = f"missing historical CNY=X rate for Qianji bill date {bill_date}"
                raise ValueError(msg)
        log.warning(
            "Qianji bill with unconverted cross-currency label (ss=%s bs=%s "
            "bv==sv=%.2f); returning source amount unchanged", ss, bs, sv,
        )
    return float(money)


def parse_qj_target_amount(money: float, extra_str: str | None) -> float:
    """Return the target-currency amount received by ``targetact`` in a transfer.

    For a cross-currency transfer, ``extra.curr.tv`` holds the amount the
    target account received in its native currency. Same-currency or
    non-transfer rows fall back to ``money`` (source amount).
    """
    curr = _decode_curr(extra_str)
    if curr is None:
        return float(money)
    ss, ts, tv = curr.get("ss"), curr.get("ts"), curr.get("tv")
    if ss and ts and ss != ts and tv is not None and tv > 0:
        return float(tv)
    return float(money)
