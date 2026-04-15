"""FidelitySource — owns all Fidelity-specific logic.

Owns:
  - Accounts_History CSV parsing (ingested into ``fidelity_transactions``).
  - Per-day position + cash query over a pre-ingested
    ``fidelity_transactions`` table, returning a unified
    ``list[PositionRow]`` that ``compute_daily_allocation`` treats
    identically to every other investment source.
  - Action classification: verbose Fidelity ``Action`` strings →
    canonical ``ACT_*`` legacy labels → normalized :class:`ActionKind`.
    ``classify_fidelity_action`` is re-exported at module level because the
    :mod:`etl.migrations.add_fidelity_action_kind` backfill still imports
    it directly.
  - T-Bill CUSIP aggregation (8+ digit symbols surface as ``T-Bills``
    with value = face quantity).
  - Mutual-fund T-1 price dating (yfinance stamps MF NAV with the wrong
    date; we look up T-1 instead).
  - Per-account cash → money-market-fund ticker routing
    (``fidelity_accounts[account_number]``, defaulting to ``FZFXX``).

``positions_at`` delegates transaction replay to the legacy
:func:`etl.timemachine.replay_from_db`. The source-agnostic
:func:`etl.replay.replay_transactions` primitive understands a narrower
action alphabet (BUY / SELL / REINVESTMENT only) than Fidelity's
transaction stream, which also includes REDEMPTION PAYOUT, TRANSFERRED
FROM/TO, DISTRIBUTION, and EXCHANGED TO — all position-affecting actions
that ``_replay_core`` handles via ``POSITION_PREFIXES``. Switching
Fidelity to the narrower primitive would change the share-count output
for real data; the migration to that primitive is a separate,
behaviour-preserving refactor.
"""
from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar

import pandas as pd

from etl.db import get_connection
from etl.parsing import STRICT_US_DATE_RE, parse_us_date
from etl.sources import (
    _REGISTRY,
    ActionKind,
    InvestmentSource,
    PositionRow,
    PriceContext,
    SourceKind,
)
from etl.types import (
    ACT_BUY,
    ACT_COLLATERAL,
    ACT_DEPOSIT,
    ACT_DISTRIBUTION,
    ACT_DIVIDEND,
    ACT_EXCHANGE,
    ACT_FOREIGN_TAX,
    ACT_INTEREST,
    ACT_IRA_CONTRIBUTION,
    ACT_LENDING,
    ACT_OTHER,
    ACT_REDEMPTION,
    ACT_REINVESTMENT,
    ACT_ROTH_CONVERSION,
    ACT_SELL,
    ACT_TRANSFER,
    ACT_WITHDRAWAL,
    FidelityTransaction,
)
from etl.types import (
    parse_float as _parse_float,
)

log = logging.getLogger(__name__)

# Default set of mutual-fund tickers that need T-1 price lookup. Mirrors the
# legacy ``allocation._MUTUAL_FUNDS`` constant so that behaviour stays
# unchanged when ``from_raw_config`` is called without an explicit
# ``mutual_funds`` key.
_DEFAULT_MUTUAL_FUNDS: frozenset[str] = frozenset({"FXAIX", "FSSNX", "FNJHX"})


# ── Action classification ───────────────────────────────────────────────────

_ACTION_RULES: list[tuple[str, str]] = [
    # Order matters: more specific patterns first
    ("FOREIGN TAX", ACT_FOREIGN_TAX),
    ("REINVESTMENT", ACT_REINVESTMENT),
    ("DIVIDEND RECEIVED", ACT_DIVIDEND),
    ("REDEMPTION PAYOUT", ACT_REDEMPTION),
    ("YOU BOUGHT", ACT_BUY),
    ("YOU SOLD", ACT_SELL),
    ("DISTRIBUTION", ACT_DISTRIBUTION),
    ("EXCHANGED TO", ACT_EXCHANGE),
    ("Electronic Funds Transfer", ACT_DEPOSIT),
    ("DIRECT DEPOSIT", ACT_DEPOSIT),
    ("CASH CONTRIBUTION", ACT_IRA_CONTRIBUTION),
    ("CONV TO ROTH", ACT_ROTH_CONVERSION),
    ("ROTH CONVERSION", ACT_ROTH_CONVERSION),
    ("EARLY DIST", ACT_TRANSFER),
    ("PARTIAL CY RECHAR", ACT_TRANSFER),
    ("ROLLOVER CASH", ACT_TRANSFER),
    ("TRANSFERRED", ACT_TRANSFER),
    ("INTEREST", ACT_INTEREST),
    ("YOU LOANED", ACT_LENDING),
    ("LOAN RETURNED", ACT_LENDING),
    ("INCREASE COLLATERAL", ACT_COLLATERAL),
    ("DECREASE COLLATERAL", ACT_COLLATERAL),
    ("CASH ADVANCE", ACT_WITHDRAWAL),
    ("DIRECT DEBIT", ACT_WITHDRAWAL),
    ("DEBIT CARD", ACT_WITHDRAWAL),
]


def _classify_action(raw_action: str) -> str:
    """Map a verbose Fidelity action string to a canonical action_type."""
    upper = raw_action.upper()
    for pattern, action_type in _ACTION_RULES:
        if pattern.upper() in upper:
            return action_type
    return ACT_OTHER


# Mapping from Fidelity's canonical action_type constants to the normalized
# ActionKind enum consumed by the source-agnostic replay primitive
# (etl/replay.py). Kept in sync with ``_classify_action`` so Fidelity's
# classification rules stay the single source of truth.
_ACTION_TYPE_TO_KIND: dict[str, ActionKind] = {
    ACT_BUY: ActionKind.BUY,
    ACT_SELL: ActionKind.SELL,
    ACT_DIVIDEND: ActionKind.DIVIDEND,
    ACT_REINVESTMENT: ActionKind.REINVESTMENT,
    ACT_DEPOSIT: ActionKind.DEPOSIT,
    ACT_WITHDRAWAL: ActionKind.WITHDRAWAL,
    # Internal retirement/account transfers all map to TRANSFER — they move
    # cash between accounts but neither deposit nor withdraw at the portfolio
    # level. Rollovers, partial recharacterizations, Roth conversions.
    ACT_TRANSFER: ActionKind.TRANSFER,
    ACT_ROTH_CONVERSION: ActionKind.TRANSFER,
    # IRA cash contributions function as deposits into the retirement account.
    ACT_IRA_CONTRIBUTION: ActionKind.DEPOSIT,
    # Position-prefix-but-not-cost-basis-impacting actions (REDEMPTION PAYOUT,
    # TRANSFERRED FROM/TO, DISTRIBUTION, EXCHANGED TO) are bucketed as OTHER
    # for the shared primitive. ``_replay_core`` in :mod:`etl.timemachine`
    # still handles their qty effects directly via the raw action string; the
    # primitive is intentionally conservative here.
    ACT_DISTRIBUTION: ActionKind.OTHER,
    ACT_REDEMPTION: ActionKind.OTHER,
    ACT_EXCHANGE: ActionKind.OTHER,
    # Non-position, non-cashflow pass-throughs.
    ACT_INTEREST: ActionKind.OTHER,
    ACT_FOREIGN_TAX: ActionKind.OTHER,
    ACT_LENDING: ActionKind.OTHER,
    ACT_COLLATERAL: ActionKind.OTHER,
    ACT_OTHER: ActionKind.OTHER,
}


def classify_fidelity_action(raw_action: str) -> ActionKind:
    """Map a verbose Fidelity action string to the normalized :class:`ActionKind`.

    Unknown action types fall through to :attr:`ActionKind.OTHER`.
    """
    return _ACTION_TYPE_TO_KIND.get(_classify_action(raw_action), ActionKind.OTHER)


_CSV_DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")


def _csv_earliest_date(path: Path) -> str:
    """Return earliest ``YYYYMMDD`` date in a CSV for chronological sorting.

    Raw Fidelity CSVs carry ``MM/DD/YYYY`` dates; convert to ``YYYYMMDD`` for
    a lexicographically sortable key so ``sorted(..., key=_csv_earliest_date)``
    picks the oldest-first ordering. Paths with no recognizable dates sort
    last (very large sentinel).
    """
    text = path.read_text(encoding="utf-8-sig")
    dates: list[str] = _CSV_DATE_RE.findall(text)
    if not dates:
        return "99999999"
    return min(d[6:10] + d[0:2] + d[3:5] for d in dates)


# ── CSV → in-memory transaction list ──────────────────────────────────────
# ``load_transactions`` / ``_parse_csv_text`` preserve the pre-refactor API
# consumed by contract and unit tests. They are free-standing because callers
# sometimes want to inspect the parsed rows without touching a database.


def _parse_csv_text(text: str, *, source: str = "CSV text") -> list[FidelityTransaction]:
    """Parse Fidelity history CSV text into structured transaction records."""
    if text.startswith("\ufeff"):
        text = text[1:]

    lines = text.splitlines(keepends=True)

    # Skip leading blank lines to find the header.
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1

    csv_text = "".join(lines[start:])
    reader = csv.DictReader(csv_text.splitlines())

    transactions: list[FidelityTransaction] = []
    for row_num, row in enumerate(reader, start=2):
        run_date = (row.get("Run Date") or "").strip()
        if not run_date:
            continue
        # Skip footer/disclaimer rows — anything that doesn't look like a
        # date is assumed to be narrative text and is dropped silently.
        if not STRICT_US_DATE_RE.match(run_date):
            continue

        iso_date = parse_us_date(run_date, strict=True, row_context=f"{source} row {row_num}")

        raw_action = (row.get("Action") or "").strip()
        symbol = (row.get("Symbol") or "").strip()
        description = (row.get("Description") or "").strip()
        account = (row.get("Account Number") or "").strip()
        lot_type = (row.get("Type") or "").strip()
        quantity = _parse_float(row.get("Quantity", ""))
        price = _parse_float(row.get("Price", ""))
        amount = _parse_float(row.get("Amount", ""))
        action_type = _classify_action(raw_action)
        # EFT with negative amount is a withdrawal, not a deposit.
        if action_type == ACT_DEPOSIT and amount < 0:
            action_type = ACT_WITHDRAWAL

        txn: FidelityTransaction = {
            "date": iso_date,
            "account": account,
            "action_type": action_type,
            "symbol": symbol,
            "description": description,
            "lot_type": lot_type,
            "quantity": quantity,
            "price": price,
            "amount": amount,
            "raw_action": raw_action,
            "dedup_key": (iso_date, account, raw_action, symbol, amount),
        }
        transactions.append(txn)

    return transactions


def load_transactions(csv_path: Path) -> list[FidelityTransaction]:
    """Load Fidelity Accounts History CSV from a file path into memory."""
    txns = _parse_csv_text(csv_path.read_text(encoding="utf-8-sig"), source=csv_path.name)
    by_type: dict[str, int] = {}
    for t in txns:
        by_type[t["action_type"]] = by_type.get(t["action_type"], 0) + 1
    log.info(
        "Transactions: %d from %s (%s)",
        len(txns), csv_path.name,
        ", ".join(f"{t}={c}" for t, c in sorted(by_type.items())),
    )
    return txns


# ── Per-source config ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class FidelitySourceConfig:
    downloads_dir: Path
    fidelity_accounts: dict[str, str]  # account_number → money-market fund ticker
    mutual_funds: frozenset[str]
    table: str = "fidelity_transactions"


# ── Concrete source ────────────────────────────────────────────────────────


class FidelitySource:
    kind: ClassVar[SourceKind] = SourceKind.FIDELITY

    def __init__(self, config: FidelitySourceConfig, db_path: Path) -> None:
        self._config = config
        self._db_path = db_path

    @classmethod
    def from_raw_config(
        cls, raw: dict[str, object], db_path: Path
    ) -> FidelitySource:
        """Build a FidelitySource from the raw ``config.json`` shape.

        Reads ``fidelity_downloads`` (CSV directory), ``fidelity_accounts``
        (per-account MM fund mapping), and ``mutual_funds`` (T-1 price
        tickers). Missing keys fall back to sensible defaults that match
        the pre-refactor ``allocation.py`` behaviour.
        """
        downloads_raw = raw.get("fidelity_downloads")
        downloads_dir = Path(downloads_raw) if isinstance(downloads_raw, (str, Path)) else Path.home() / "Downloads"
        accounts_raw = raw.get("fidelity_accounts") or {}
        accounts = dict(accounts_raw) if isinstance(accounts_raw, dict) else {}
        mfs_raw = raw.get("mutual_funds")
        if mfs_raw is None:
            mutual_funds = _DEFAULT_MUTUAL_FUNDS
        elif isinstance(mfs_raw, (list, tuple, set, frozenset)):
            mutual_funds = frozenset(mfs_raw)
        else:
            mutual_funds = _DEFAULT_MUTUAL_FUNDS
        return cls(
            FidelitySourceConfig(
                downloads_dir=downloads_dir,
                fidelity_accounts=accounts,
                mutual_funds=mutual_funds,
            ),
            db_path,
        )

    def ingest(self) -> None:
        """Scan ``downloads_dir`` for ``Accounts_History*.csv`` and ingest each file.

        Files are processed in chronological order by earliest ``MM/DD/YYYY``
        date in their body. Each CSV is authoritative for its own date range,
        so processing oldest→newest naturally deduplicates overlapping
        exports via :meth:`_ingest_one_csv`'s range-replace.
        """
        raw_csvs = sorted(self._config.downloads_dir.glob("Accounts_History*.csv"))
        raw_csvs.sort(key=_csv_earliest_date)
        for csv_path in raw_csvs:
            self._ingest_one_csv(csv_path)

    def _ingest_one_csv(self, csv_path: Path) -> int:
        """Ingest one Fidelity CSV, replacing overlapping date ranges.

        Each Fidelity CSV export is an authoritative snapshot of its own date
        range, so a new CSV's contents supersede any existing rows in that
        range. We DELETE rows whose ``run_date`` falls within this CSV's
        min/max and then INSERT every parsed row. No row-level dedup: two
        legitimate trades with identical
        ``(run_date, action, symbol, quantity, price, amount)`` are
        indistinguishable from CSV alone and preserving both matches reality
        better than collapsing them. Use ``scripts/verify_positions.py``
        against a ``Portfolio_Positions_*.csv`` snapshot to confirm
        share-count invariants.

        Dates are normalized from Fidelity's ``MM/DD/YYYY`` to ISO
        ``YYYY-MM-DD`` at write time so the database only ever carries ISO
        dates.

        Returns the total row count in ``fidelity_transactions`` after
        ingestion.
        """
        text = csv_path.read_text(encoding="utf-8-sig")
        lines = text.splitlines()

        # Find the header line (starts with "Run Date").
        header_idx = -1
        for i, line in enumerate(lines):
            if line.strip().startswith("Run Date"):
                header_idx = i
                break
        if header_idx == -1:
            msg = f"No header row found in {csv_path}"
            raise ValueError(msg)

        reader = csv.DictReader(lines[header_idx:])
        rows: list[tuple[str, str, str, str, str, str, str, str, str, float, float, float, str]] = []
        iso_dates: list[str] = []

        # DictReader consumes the header, so the first data row is file
        # line header_idx + 2.
        for offset, record in enumerate(reader):
            run_date_raw = record.get("Run Date", "").strip()
            # Skip blank rows and footer/disclaimer text; only rows shaped
            # like a Fidelity date participate in ingestion.
            if not STRICT_US_DATE_RE.match(run_date_raw):
                continue

            iso_date = parse_us_date(
                run_date_raw,
                strict=True,
                row_context=f"{csv_path.name} line {header_idx + 2 + offset}",
            )

            raw_action = record.get("Action", "").strip().strip('"')
            rows.append((
                iso_date,
                record.get("Account", "").strip().strip('"'),
                record.get("Account Number", "").strip().strip('"'),
                raw_action,
                _classify_action(raw_action),
                classify_fidelity_action(raw_action).value,
                record.get("Symbol", "").strip(),
                record.get("Description", "").strip().strip('"'),
                record.get("Type", "").strip(),
                _parse_float(record.get("Quantity", "")),
                _parse_float(record.get("Price", "")),
                _parse_float(record.get("Amount", "")),
                record.get("Settlement Date", "").strip(),
            ))
            iso_dates.append(iso_date)

        if not rows:
            conn = get_connection(self._db_path)
            count: int = conn.execute(
                f"SELECT COUNT(*) FROM {self._config.table}"  # noqa: S608 — table is a trusted ClassVar
            ).fetchone()[0]
            conn.close()
            return count

        min_date = min(iso_dates)
        max_date = max(iso_dates)

        conn = get_connection(self._db_path)
        try:
            conn.execute(
                f"DELETE FROM {self._config.table} WHERE run_date BETWEEN ? AND ?",  # noqa: S608
                (min_date, max_date),
            )
            conn.executemany(
                f"""INSERT INTO {self._config.table}
                    (run_date, account, account_number, action, action_type, action_kind,
                     symbol, description, lot_type, quantity, price, amount, settlement_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",  # noqa: S608
                rows,
            )
            conn.commit()

            count = conn.execute(
                f"SELECT COUNT(*) FROM {self._config.table}"  # noqa: S608
            ).fetchone()[0]
        finally:
            conn.close()

        return count

    def positions_at(self, as_of: date, prices: PriceContext) -> list[PositionRow]:
        """Return one PositionRow per (account, ticker) position + cash bucket.

        Reuses :func:`etl.timemachine.replay_from_db` for the core
        cost-basis accumulator. That function understands the full Fidelity
        action alphabet (BUY / SELL / REINVESTMENT plus REDEMPTION PAYOUT,
        TRANSFERRED FROM/TO, DISTRIBUTION, EXCHANGED TO) and correctly
        excludes money-market symbols from position accumulation. The
        narrower :func:`etl.replay.replay_transactions` primitive is not
        yet sufficient; migrating to it is a separate refactor.
        """
        from etl.timemachine import replay_from_db

        result = replay_from_db(self._db_path, as_of)
        positions: dict[tuple[str, str], float] = result["positions"]
        cash_by_account: dict[str, float] = result["cash"]
        cost_basis: dict[tuple[str, str], float] = result.get("cost_basis") or {}

        rows: list[PositionRow] = []

        # ── Positions (one row per (account, ticker); may emit multiple
        # ── PositionRows with the same ``ticker`` when the same symbol is
        # ── held in more than one account — the caller aggregates by ticker).
        for (acct, sym), qty in positions.items():
            cb = cost_basis.get((acct, sym))

            if sym and sym[0].isdigit() and len(sym) >= 8:
                # T-Bill CUSIP: face value quantity, bucketed under "T-Bills".
                rows.append(PositionRow(
                    ticker="T-Bills",
                    value_usd=qty,
                    quantity=qty,
                    cost_basis_usd=cb,
                    account=acct,
                ))
                continue

            # Regular symbol: price-lookup against PriceContext. Missing
            # prices get logged (mirrors the legacy ``_add_fidelity_positions``
            # warning) and the row is excluded from the output.
            p_date = prices.mf_price_date if sym in self._config.mutual_funds else prices.price_date
            if sym in prices.prices.columns and p_date in prices.prices.index:
                price = prices.prices.loc[p_date, sym]
                if pd.notna(price):
                    rows.append(PositionRow(
                        ticker=sym,
                        value_usd=qty * float(price),
                        quantity=qty,
                        cost_basis_usd=cb,
                        account=acct,
                    ))
                    continue
            log.warning(
                "No price for %s on %s (holding %.3f shares) — excluded from allocation",
                sym, p_date, qty,
            )

        # ── Per-account cash routed to each account's MM fund.
        for acct, bal in cash_by_account.items():
            mm_ticker = self._config.fidelity_accounts.get(acct, "FZFXX")
            rows.append(PositionRow(
                ticker=mm_ticker,
                value_usd=bal,
                account=acct,
            ))

        return rows


# Register this class in the central registry at import time.
if FidelitySource not in _REGISTRY:
    _REGISTRY.append(FidelitySource)


_: type[InvestmentSource] = FidelitySource  # structural-subtype sanity check
