"""Robinhood source — persists transactions to robinhood_transactions and uses shared replay.

Replaces the on-the-fly CSV replay from the now-removed
``etl/ingest/robinhood_history.py``. Robinhood is the first real consumer of
the source-agnostic :func:`etl.replay.replay_transactions` primitive:
ingestion writes rows into the ``robinhood_transactions`` table with
primitive-native columns (``txn_date``, ``action_kind``, ``ticker``,
``quantity``, ``amount_usd``), and :func:`positions_at` delegates to the
primitive to accumulate quantity as of any date.

Action classification note: Robinhood's ``REC`` rows (shares received from
stock transfers/dividends) carry real ``quantity`` but no amount. We normalize
them as ``ActionKind.BUY`` with ``amount_usd = 0`` so replay adds shares.
"""
from __future__ import annotations

import csv
import logging
import re
from datetime import date, datetime
from pathlib import Path

from etl.db import get_connection
from etl.replay import ReplayConfig, replay_transactions
from etl.sources._types import ActionKind, PositionRow, PriceContext
from etl.types import RawConfig, parse_currency

log = logging.getLogger(__name__)

TABLE = "robinhood_transactions"

# Per-source replay config — passed to :func:`etl.replay.replay_transactions`.
# Robinhood's table already uses the primitive's default column names
# (``txn_date`` / ``ticker`` / ``amount_usd``) and has no account grouping.
ROBINHOOD_REPLAY = ReplayConfig(table=TABLE)


# ── Action classification ──────────────────────────────────────────────────


# Robinhood ``Trans Code`` → normalized :class:`ActionKind`.
#
# ``REC`` (received stock) is deliberately mapped to BUY: REC rows carry real
# share quantity with ``Amount = 0``, so the primitive adds quantity.
_ACTION_MAP: dict[str, ActionKind] = {
    "Buy": ActionKind.BUY,
    "Sell": ActionKind.SELL,
    "CDIV": ActionKind.DIVIDEND,
    "DRIP": ActionKind.REINVESTMENT,
    "ACH": ActionKind.DEPOSIT,
    "REC": ActionKind.BUY,
    # SLIP (stock-lending payment), AFEE/DFEE (ADR fees), RTP (return of
    # principal) have no per-ticker position impact and fall through to OTHER.
}


def classify_robinhood_action(trans_code: str) -> ActionKind:
    """Map a raw Robinhood ``Trans Code`` to the normalized :class:`ActionKind`."""
    return _ACTION_MAP.get(trans_code.strip(), ActionKind.OTHER)


# ── Config helpers ─────────────────────────────────────────────────────────


def _csv_paths(downloads: Path) -> list[Path]:
    """Glob matching Robinhood activity-report CSVs for this build.

    Returns ``[]`` when the directory doesn't exist. Users without a Robinhood
    CSV see no rows; users with multiple exports (e.g. quarterly pulls) have
    each CSV range-replace its own window.
    """
    if not downloads.exists():
        return []
    return sorted(downloads.glob("Robinhood_history*.csv"))


# ── Public API (module protocol) ───────────────────────────────────────────


def ingest(db_path: Path, downloads: Path) -> None:
    """Scan ``downloads`` for ``Robinhood_history*.csv`` and ingest each.

    Each CSV is authoritative for its own date window via
    :func:`_ingest_one_csv`'s range-replace. Re-running the build on the same
    set of CSVs yields bit-identical DB state. Legitimate same-day duplicate
    trades are preserved — Robinhood CSVs do occasionally emit two rows with
    identical date/ticker/action/qty/amount, and silently collapsing one would
    understate positions.

    If no CSV matches (user doesn't have Robinhood), this is a silent no-op.
    """
    for path in _csv_paths(downloads):
        _ingest_one_csv(db_path, path)


def _ingest_one_csv(db_path: Path, csv_path: Path) -> None:
    """Parse one Robinhood CSV and persist its rows via range-replace."""
    rows: list[tuple[str, str, str, str, float, float, str]] = []
    for row in csv.DictReader(csv_path.read_text(encoding="utf-8-sig").splitlines()):
        activity_date = (row.get("Activity Date") or "").strip()
        # Skip blank / footer rows — only rows with a parseable
        # MM/DD/YYYY (or M/D/YYYY) date are real transactions.
        if not re.match(r"\d{1,2}/\d{1,2}/\d{4}", activity_date):
            continue

        d = datetime.strptime(activity_date, "%m/%d/%Y").date()
        action_raw = (row.get("Trans Code") or "").strip()
        kind = classify_robinhood_action(action_raw)
        ticker = (row.get("Instrument") or "").strip()
        quantity = parse_currency(row.get("Quantity", ""))
        # Canonical sign convention (shared with Fidelity + consumed by
        # :func:`etl.replay.replay_transactions`): BUY qty > 0, SELL qty < 0.
        # Robinhood's CSV stores SELL qty as positive, so normalize at ingest.
        if kind == ActionKind.SELL:
            quantity = -abs(quantity)
        amount = parse_currency(row.get("Amount", ""))
        description = (row.get("Description") or "").strip()
        rows.append((
            d.isoformat(),
            action_raw,
            kind.value,
            ticker,
            quantity,
            amount,
            description,
        ))

    conn = get_connection(db_path)
    try:
        if rows:
            dates = [r[0] for r in rows]
            conn.execute(
                f"DELETE FROM {TABLE} WHERE txn_date BETWEEN ? AND ?",  # noqa: S608
                (min(dates), max(dates)),
            )
            conn.executemany(
                f"INSERT INTO {TABLE} "  # noqa: S608 — TABLE is a module-level constant
                "(txn_date, action, action_kind, ticker, quantity, amount_usd, raw_description) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        conn.commit()
    finally:
        conn.close()


def positions_at(
    db_path: Path,
    as_of: date,
    prices: PriceContext,
    config: RawConfig,
) -> list[PositionRow]:
    """Return one :class:`PositionRow` per non-zero ticker position as of ``as_of``.

    Delegates quantity accumulation to the shared
    :func:`etl.replay.replay_transactions` primitive, then values each
    position with today's close from :class:`PriceContext`.

    Tickers with no price on ``price_date`` are logged and excluded.
    """
    del config  # Robinhood has no per-call config knobs.
    result = replay_transactions(db_path, ROBINHOOD_REPLAY, as_of)
    rows: list[PositionRow] = []
    # Robinhood has no account column — the primitive groups every row under
    # the empty account key, so the tuple's account component is discarded.
    for (_acct, ticker), st in result.positions.items():
        price = prices.lookup(ticker)
        if price is not None:
            rows.append(PositionRow(
                ticker=ticker,
                value_usd=st.quantity * price,
            ))
            continue
        if prices.should_warn_once("robinhood_missing_price", ticker):
            log.warning(
                "No Robinhood price for %s on %s (holding %.3f shares) — excluded from allocation",
                ticker, prices.price_date, st.quantity,
            )
    return rows
