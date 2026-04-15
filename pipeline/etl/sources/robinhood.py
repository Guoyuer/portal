"""Robinhood source — persists transactions to robinhood_transactions and uses shared replay.

Replaces the on-the-fly CSV replay that previously existed in
``etl/ingest/robinhood_history.py``. Robinhood is the first real consumer of
the source-agnostic :func:`etl.replay.replay_transactions` primitive:
ingestion writes rows into the ``robinhood_transactions`` table with
primitive-native columns (``txn_date``, ``action_kind``, ``ticker``,
``quantity``, ``amount_usd``), and :func:`positions_at` delegates to the
primitive to accumulate quantity + cost basis as of any date.

Action classification note: Robinhood's ``REC`` rows (shares received from
stock transfers/dividends) carry real ``quantity`` but no cost basis. The
legacy ``replay_robinhood`` treated them as position-increments without
cost-basis updates. To preserve that behaviour through the primitive without
widening the :class:`ActionKind` alphabet, we normalize ``REC`` rows as
``ActionKind.BUY`` with ``amount_usd = 0``. The primitive's BUY rule adds
``abs(amt)`` to cost (adding 0 is a no-op) and ``q`` to quantity — identical
to the legacy behaviour.
"""
from __future__ import annotations

import csv
import logging
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

from etl.replay import replay_transactions
from etl.sources import ActionKind, PositionRow, PriceContext
from etl.sources._ingest import range_replace_insert

log = logging.getLogger(__name__)

TABLE = "robinhood_transactions"


# ── Amount parsing ─────────────────────────────────────────────────────────


_PARENS_AMOUNT = re.compile(r"^\(([\d.,]+)\)$")


def _parse_amount(raw: str) -> float:
    """Parse a Robinhood ``Amount`` column: ``$1,234.56`` or ``($1,234.56)`` (negative)."""
    s = (raw or "").strip()
    if not s:
        return 0.0
    cleaned = s.replace("$", "").replace(",", "")
    m = _PARENS_AMOUNT.match(cleaned)
    if m:
        return -float(m.group(1))
    return float(cleaned)


# ── Action classification ──────────────────────────────────────────────────


# Robinhood ``Trans Code`` → normalized :class:`ActionKind`.
#
# ``REC`` (received stock) is deliberately mapped to BUY: REC rows carry real
# share quantity with ``Amount = 0``, so the primitive's BUY rule
# (``cost += abs(amt); qty += q``) produces the legacy behaviour (add qty, no
# cost-basis change) when ``amount_usd = 0``.
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


def _parse_float(raw: str) -> float:
    """Parse Robinhood's ``Quantity``/``Price`` column — strips ``$`` and ``,``."""
    s = (raw or "").strip().replace("$", "").replace(",", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


# ── Config helpers ─────────────────────────────────────────────────────────


def _downloads_dir(config: dict[str, object]) -> Path:
    """Resolve the directory that holds ``Robinhood_history*.csv`` exports.

    Prefers a dedicated ``robinhood_downloads`` key, then ``fidelity_downloads``
    (same ``~/Downloads`` folder in practice), then the system Downloads
    folder. Every fallback goes through :meth:`Path.exists` in the callers,
    so a missing config / directory surfaces as a silent no-op.
    """
    for key in ("robinhood_downloads", "fidelity_downloads"):
        raw = config.get(key)
        if isinstance(raw, (str, Path)):
            return Path(raw)
    return Path.home() / "Downloads"


def _csv_paths(config: dict[str, object]) -> list[Path]:
    """Glob matching Robinhood activity-report CSVs for this build.

    Returns ``[]`` when the directory doesn't exist. Users without a Robinhood
    CSV see no rows; users with multiple exports (e.g. quarterly pulls) have
    each CSV range-replace its own window, mirroring Fidelity's ingest.
    """
    downloads = _downloads_dir(config)
    if not downloads.exists():
        return []
    return sorted(downloads.glob("Robinhood_history*.csv"))


# ── Public API (module protocol) ───────────────────────────────────────────


def produces_positions(config: dict[str, object]) -> bool:
    """Always on. The ingest path is a silent no-op when no CSVs are present,
    and :func:`positions_at` returns an empty list when the table is empty.
    """
    del config
    return True


def ingest(db_path: Path, config: dict[str, object]) -> None:
    """Scan ``robinhood_downloads`` for ``Robinhood_history*.csv`` and ingest each.

    Each CSV is authoritative for its own date window via
    :func:`_ingest_one_csv`'s range-replace (mirrors
    :func:`etl.sources.fidelity.parse._ingest_one_csv`). Re-running the build
    on the same set of CSVs yields bit-identical DB state. Legitimate same-
    day duplicate trades are preserved — Robinhood CSVs do occasionally emit
    two rows with identical date/ticker/action/qty/amount, and silently
    collapsing one would understate positions.

    If no CSV matches (user doesn't have Robinhood), this is a silent no-op.
    """
    for path in _csv_paths(config):
        _ingest_one_csv(db_path, path)


def _ingest_one_csv(db_path: Path, csv_path: Path) -> None:
    """Parse one Robinhood CSV and persist its rows via range-replace."""
    text = csv_path.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    rows: list[tuple[str, str, str, str, float, float, str]] = []
    for row in reader:
        activity_date = (row.get("Activity Date") or "").strip()
        # Skip blank / footer rows — only rows with a parseable
        # MM/DD/YYYY (or M/D/YYYY) date are real transactions.
        if not re.match(r"\d{1,2}/\d{1,2}/\d{4}", activity_date):
            continue

        d = datetime.strptime(activity_date, "%m/%d/%Y").date()
        action_raw = (row.get("Trans Code") or "").strip()
        kind = classify_robinhood_action(action_raw)
        ticker = (row.get("Instrument") or "").strip()
        quantity = _parse_float(row.get("Quantity", ""))
        # Canonical sign convention (shared with Fidelity + consumed by
        # :func:`etl.replay.replay_transactions`): BUY qty > 0, SELL qty < 0.
        # Robinhood's CSV stores SELL qty as positive, so normalize at ingest.
        if kind == ActionKind.SELL:
            quantity = -abs(quantity)
        amount = _parse_amount(row.get("Amount", ""))
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

    conn = sqlite3.connect(str(db_path))
    try:
        # Range-replace: wipe any existing rows in the CSV's window, then
        # insert the fresh set. Protects against partial CSVs (e.g. a 3-
        # month export later replaced by a 12-month export with updated
        # back-fills) leaving stale rows behind.
        range_replace_insert(
            conn,
            table=TABLE,
            date_col="txn_date",
            rows=rows,
            date_idx=0,
            insert_sql=(
                f"INSERT INTO {TABLE} "  # noqa: S608 — TABLE is a module-level constant
                "(txn_date, action, action_kind, ticker, quantity, amount_usd, raw_description) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)"
            ),
        )
        conn.commit()
    finally:
        conn.close()


def positions_at(
    db_path: Path,
    as_of: date,
    prices: PriceContext,
    config: dict[str, object],
) -> list[PositionRow]:
    """Return one :class:`PositionRow` per non-zero ticker position as of ``as_of``.

    Delegates quantity + cost-basis accumulation to the shared
    :func:`etl.replay.replay_transactions` primitive, then projects each
    :class:`PositionState` into a :class:`PositionRow` by looking up
    today's close from :class:`PriceContext`.

    Tickers with no price on ``price_date`` are logged and excluded —
    mirroring Fidelity's behaviour.
    """
    del config  # Robinhood has no per-call config knobs.
    states = replay_transactions(db_path, TABLE, as_of)
    rows: list[PositionRow] = []
    for ticker, st in states.items():
        price = prices.lookup(ticker)
        if price is not None:
            rows.append(PositionRow(
                ticker=ticker,
                value_usd=st.quantity * price,
                quantity=st.quantity,
                cost_basis_usd=st.cost_basis_usd,
            ))
            continue
        log.warning(
            "No Robinhood price for %s on %s (holding %.3f shares) — excluded from allocation",
            ticker, prices.price_date, st.quantity,
        )
    return rows
