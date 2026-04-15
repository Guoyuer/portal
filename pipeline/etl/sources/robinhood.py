"""RobinhoodSource — persists transactions to robinhood_transactions and uses shared replay.

Replaces the on-the-fly CSV replay that previously existed in
``etl/ingest/robinhood_history.py``. Robinhood is the first real consumer of
the source-agnostic :func:`etl.replay.replay_transactions` primitive:
ingestion writes rows into the ``robinhood_transactions`` table with
primitive-native columns (``txn_date``, ``action_kind``, ``ticker``,
``quantity``, ``amount_usd``), and :meth:`positions_at` delegates to the
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
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import ClassVar

import pandas as pd

from etl.replay import replay_transactions
from etl.sources import (
    _REGISTRY,
    ActionKind,
    InvestmentSource,
    PositionRow,
    PriceContext,
    SourceKind,
)

log = logging.getLogger(__name__)


# ── Amount parsing ─────────────────────────────────────────────────────────


_PARENS_AMOUNT = re.compile(r"^\(([\d.,]+)\)$")


def _parse_amount(raw: str) -> float:
    """Parse a Robinhood ``Amount`` column: ``$1,234.56`` or ``($1,234.56)`` (negative).

    Matches the legacy :func:`etl.ingest.robinhood_history._parse_amount` bit
    for bit so the migrated path produces identical cost-basis numbers.
    """
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


# ── Per-source config ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class RobinhoodSourceConfig:
    csv_path: Path
    table: str = "robinhood_transactions"


# ── Concrete source ────────────────────────────────────────────────────────


class RobinhoodSource:
    kind: ClassVar[SourceKind] = SourceKind.ROBINHOOD

    def __init__(self, config: RobinhoodSourceConfig, db_path: Path) -> None:
        self._config = config
        self._db_path = db_path

    @classmethod
    def from_raw_config(cls, raw: dict[str, object], db_path: Path) -> RobinhoodSource:
        """Build a RobinhoodSource from the raw config dict.

        Reads ``robinhood_csv``. If the key is missing or empty, the source
        falls back to a non-existent path so :meth:`ingest` + :meth:`positions_at`
        become no-ops for users who don't have a Robinhood CSV.
        """
        raw_path = raw.get("robinhood_csv")
        if isinstance(raw_path, (str, Path)):
            csv_path = Path(raw_path)
        else:
            csv_path = Path("__missing_robinhood_csv__")
        return cls(RobinhoodSourceConfig(csv_path=csv_path), db_path)

    def ingest(self) -> None:
        """Parse the CSV and persist rows into ``robinhood_transactions``.

        Idempotent: the ``UNIQUE(txn_date, ticker, action, quantity, amount_usd)``
        constraint plus ``INSERT OR IGNORE`` means running ingest twice over
        the same CSV leaves the table unchanged. If the CSV file doesn't
        exist (user doesn't use Robinhood), this is a silent no-op.
        """
        if not self._config.csv_path.exists():
            return

        conn = sqlite3.connect(str(self._db_path))
        try:
            text = self._config.csv_path.read_text(encoding="utf-8-sig")
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
                quantity = self._parse_float(row.get("Quantity", ""))
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

            conn.executemany(
                f"INSERT OR IGNORE INTO {self._config.table} "  # noqa: S608 — table name is a trusted ClassVar
                "(txn_date, action, action_kind, ticker, quantity, amount_usd, raw_description) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _parse_float(raw: str) -> float:
        """Parse Robinhood's ``Quantity``/``Price`` column — strips ``$`` and ``,``."""
        s = (raw or "").strip().replace("$", "").replace(",", "")
        if not s:
            return 0.0
        try:
            return float(s)
        except ValueError:
            return 0.0

    def positions_at(self, as_of: date, prices: PriceContext) -> list[PositionRow]:
        """Return one :class:`PositionRow` per non-zero ticker position as of ``as_of``.

        Delegates quantity + cost-basis accumulation to the shared
        :func:`etl.replay.replay_transactions` primitive, then projects each
        :class:`PositionState` into a :class:`PositionRow` by looking up
        today's close from :class:`PriceContext`.

        Tickers with no price on ``price_date`` are logged and excluded —
        mirroring :class:`FidelitySource`'s behaviour.
        """
        states = replay_transactions(self._db_path, self._config.table, as_of)
        rows: list[PositionRow] = []
        for ticker, st in states.items():
            if ticker in prices.prices.columns and prices.price_date in prices.prices.index:
                p = prices.prices.loc[prices.price_date, ticker]
                if pd.notna(p):
                    rows.append(PositionRow(
                        ticker=ticker,
                        value_usd=st.quantity * float(p),
                        quantity=st.quantity,
                        cost_basis_usd=st.cost_basis_usd,
                    ))
                    continue
            log.warning(
                "No Robinhood price for %s on %s (holding %.3f shares) — excluded from allocation",
                ticker, prices.price_date, st.quantity,
            )
        return rows


# Register this class in the central registry at import time.
if RobinhoodSource not in _REGISTRY:
    _REGISTRY.append(RobinhoodSource)


_: type[InvestmentSource] = RobinhoodSource  # structural-subtype sanity check
