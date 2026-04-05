"""Cross-system and portfolio reconciliation logic.

Cross-reconciliation: match Qianji transfers to Fidelity deposits by (date, amount).
Portfolio reconciliation: break down value changes by tier (fidelity/linked/manual).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)

from ..types import ACT_BUY, ACT_DEPOSIT, ACT_DIVIDEND, ACT_REINVESTMENT, ACT_SELL, Config, FidelityTransaction

# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class ReconciliationMatch:
    """A matched pair between Qianji and Fidelity."""

    date_qianji: str
    date_fidelity: str
    amount: float
    qianji_note: str
    fidelity_desc: str


@dataclass
class CrossReconciliationData:
    """Cross-system reconciliation between Qianji and Fidelity."""

    matched: list[ReconciliationMatch] = field(default_factory=list)
    unmatched_qianji: list[dict[str, Any]] = field(default_factory=list)
    unmatched_fidelity: list[dict[str, Any]] = field(default_factory=list)
    qianji_total: float = 0.0
    fidelity_total: float = 0.0
    unmatched_amount: float = 0.0


@dataclass
class TierReconciliation:
    """Reconciliation for one tier of assets."""

    start_value: float
    end_value: float
    net_change: float
    details: dict[str, Any]


@dataclass
class ReconciliationData:
    """Portfolio value reconciliation between two snapshots."""

    prev_date: str
    curr_date: str
    fidelity: TierReconciliation
    linked: TierReconciliation
    manual: TierReconciliation
    total_start: float
    total_end: float
    total_change: float


# ── Helpers ──────────────────────────────────────────────────────────────────


def _parse_date(date_str: str) -> datetime:
    """Parse a YYYY-MM-DD date string."""
    return datetime.strptime(date_str, "%Y-%m-%d")


def _get_source(ticker: str, config: Config) -> str:
    """Return the source tier for a ticker: 'fidelity', 'linked', or 'manual'.

    Defaults to 'fidelity' if not specified.
    """
    asset_info = config["assets"].get(ticker)
    if asset_info is None:
        return "fidelity"
    return asset_info.get("source", "fidelity")


# ── Cross-reconciliation ────────────────────────────────────────────────────


def cross_reconcile(
    qianji_transfers: list[dict[str, Any]],
    fidelity_deposits: list[dict[str, Any]],
    tolerance_days: int = 1,
) -> CrossReconciliationData:
    """Match Qianji transfers to Fidelity deposits by (date +/- tolerance, amount).

    Greedy algorithm: for each Qianji transfer (oldest first), find the first
    Fidelity deposit within the date tolerance with an exact amount match.
    """
    qianji_total = sum(t["amount"] for t in qianji_transfers)
    fidelity_total = sum(d["amount"] for d in fidelity_deposits)

    # Sort both lists by date
    sorted_qj = sorted(qianji_transfers, key=lambda t: t["date"])
    sorted_fd = sorted(fidelity_deposits, key=lambda d: d["date"])

    # Track which Fidelity deposits have been consumed
    fd_used: set[int] = set()
    matched: list[ReconciliationMatch] = []
    unmatched_qj: list[dict[str, Any]] = []

    tolerance = timedelta(days=tolerance_days)

    for qj in sorted_qj:
        qj_date = _parse_date(qj["date"])
        found = False

        for idx, fd in enumerate(sorted_fd):
            if idx in fd_used:
                continue
            fd_date = _parse_date(fd["date"])

            if abs(qj_date - fd_date) <= tolerance and qj["amount"] == fd["amount"]:
                matched.append(
                    ReconciliationMatch(
                        date_qianji=qj["date"],
                        date_fidelity=fd["date"],
                        amount=qj["amount"],
                        qianji_note=qj.get("note", ""),
                        fidelity_desc=fd.get("description", ""),
                    )
                )
                fd_used.add(idx)
                found = True
                break

        if not found:
            unmatched_qj.append(qj)

    unmatched_fd = [fd for i, fd in enumerate(sorted_fd) if i not in fd_used]

    unmatched_amount = sum(t["amount"] for t in unmatched_qj) + sum(d["amount"] for d in unmatched_fd)

    log.info("Cross-reconcile: %d Qianji ($%,.0f) vs %d Fidelity ($%,.0f) → %d matched, $%,.0f unmatched", len(qianji_transfers), qianji_total, len(fidelity_deposits), fidelity_total, len(matched), unmatched_amount)
    return CrossReconciliationData(
        matched=matched,
        unmatched_qianji=unmatched_qj,
        unmatched_fidelity=unmatched_fd,
        qianji_total=qianji_total,
        fidelity_total=fidelity_total,
        unmatched_amount=unmatched_amount,
    )


# ── Portfolio reconciliation ─────────────────────────────────────────────────

_DEPOSIT_TYPES = {ACT_DEPOSIT}
_DIVIDEND_TYPES = {ACT_DIVIDEND, ACT_REINVESTMENT}
_TRADE_TYPES = {ACT_BUY, ACT_SELL}


def portfolio_reconcile(
    current: dict[str, float],
    previous: dict[str, float],
    transactions: list[FidelityTransaction],
    config: Config,
) -> ReconciliationData:
    """Calculate value changes per tier, with implied market movement for Fidelity.

    Args:
        current: {ticker: value} from current positions.
        previous: {ticker: value} from previous snapshot.
        transactions: Fidelity transaction records for the period.
        config: Config dict with config["assets"][ticker]["source"].

    Returns:
        ReconciliationData with per-tier breakdown.
    """
    # Collect all tickers across both snapshots
    all_tickers = set(current.keys()) | set(previous.keys())

    # Classify tickers into tiers
    fidelity_tickers: list[str] = []
    linked_tickers: list[str] = []
    manual_tickers: list[str] = []

    for ticker in all_tickers:
        source = _get_source(ticker, config)
        if source == "linked":
            linked_tickers.append(ticker)
        elif source == "manual":
            manual_tickers.append(ticker)
        else:
            fidelity_tickers.append(ticker)

    # -- Fidelity tier --
    fid_start = sum(previous.get(t, 0.0) for t in fidelity_tickers)
    fid_end = sum(current.get(t, 0.0) for t in fidelity_tickers)
    fid_change = fid_end - fid_start

    deposits = sum(t["amount"] for t in transactions if t["action_type"] in _DEPOSIT_TYPES)
    dividends_net = sum(t["amount"] for t in transactions if t["action_type"] in _DIVIDEND_TYPES)
    # Trades (buy/sell) are internal to the account -- they don't change total value.
    trades_net = 0.0

    market_movement = fid_change - deposits - trades_net - dividends_net

    fidelity_tier = TierReconciliation(
        start_value=fid_start,
        end_value=fid_end,
        net_change=fid_change,
        details={
            "deposits": deposits,
            "trades_net": trades_net,
            "dividends_net": dividends_net,
            "market_movement": market_movement,
        },
    )

    # -- Linked tier --
    linked_start = sum(previous.get(t, 0.0) for t in linked_tickers)
    linked_end = sum(current.get(t, 0.0) for t in linked_tickers)
    linked_change = linked_end - linked_start

    linked_details = {t: current.get(t, 0.0) - previous.get(t, 0.0) for t in linked_tickers}

    linked_tier = TierReconciliation(
        start_value=linked_start,
        end_value=linked_end,
        net_change=linked_change,
        details=linked_details,
    )

    # -- Manual tier --
    manual_start = sum(previous.get(t, 0.0) for t in manual_tickers)
    manual_end = sum(current.get(t, 0.0) for t in manual_tickers)
    manual_change = manual_end - manual_start

    manual_details = {t: current.get(t, 0.0) - previous.get(t, 0.0) for t in manual_tickers}

    manual_tier = TierReconciliation(
        start_value=manual_start,
        end_value=manual_end,
        net_change=manual_change,
        details=manual_details,
    )

    # -- Totals --
    total_start = fid_start + linked_start + manual_start
    total_end = fid_end + linked_end + manual_end
    total_change = total_end - total_start

    log.info("Portfolio reconcile: fidelity Δ$%,.0f (market=$%,.0f deposits=$%,.0f), linked Δ$%,.0f, manual Δ$%,.0f, total Δ$%,.0f", fid_change, market_movement, deposits, linked_change, manual_change, total_change)
    return ReconciliationData(
        prev_date="",  # caller fills in actual dates
        curr_date="",
        fidelity=fidelity_tier,
        linked=linked_tier,
        manual=manual_tier,
        total_start=total_start,
        total_end=total_end,
        total_change=total_change,
    )
