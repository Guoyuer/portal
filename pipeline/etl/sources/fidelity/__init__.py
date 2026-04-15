"""Fidelity source — composes the ``parse`` / ``cash`` / ``pricing`` submodules.

Public surface mirrors :mod:`etl.sources.robinhood` / :mod:`etl.sources.empower`:
``produces_positions(config)``, ``ingest(db_path, config)``,
``positions_at(db_path, as_of, prices, config)``.

``positions_at`` delegates transaction replay to the source-agnostic
:func:`etl.replay.replay_transactions` primitive. The primitive now
covers Fidelity's full action vocabulary — BUY / SELL / REINVESTMENT
plus the qty-only kinds REDEMPTION / DISTRIBUTION / EXCHANGE / TRANSFER
(``REDEMPTION PAYOUT``, ``TRANSFERRED FROM/TO``, ``DISTRIBUTION``,
``EXCHANGED TO``) — and accepts ``exclude_tickers`` to filter MM fund
symbols out of share accumulation while still letting them flow through
the cash ledger.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from etl.sources import PositionRow, PriceContext

from . import cash, parse, pricing

# Re-exports consumed by tests + the Fidelity action_kind migration. Keeping
# these at package-level means ``from etl.sources.fidelity import ...`` call
# sites survive the split.
from .parse import (
    TABLE,
    _classify_action,
    _csv_earliest_date,
    _ingest_one_csv,
    _parse_csv_text,
    classify_fidelity_action,
    load_transactions,
)
from .pricing import _DEFAULT_MUTUAL_FUNDS

# Fidelity-specific money-market fund tickers. Treated as $1/share cash, so
# they stay out of the per-share position accumulator and instead flow
# through the cash ledger (and, for REINVESTMENT rows, into the MM DRIP
# adjustment that corrects for shares credited without a paired cash entry).
MM_SYMBOLS: frozenset[str] = frozenset({"SPAXX", "FZFXX", "FDRXX"})

__all__ = [
    "MM_SYMBOLS",
    "TABLE",
    "_DEFAULT_MUTUAL_FUNDS",
    "_classify_action",
    "_csv_earliest_date",
    "_ingest_one_csv",
    "_parse_csv_text",
    "classify_fidelity_action",
    "ingest",
    "load_transactions",
    "positions_at",
    "produces_positions",
]


def _downloads_dir(config: dict[str, object]) -> Path:
    raw = config.get("fidelity_downloads")
    if isinstance(raw, (str, Path)):
        return Path(raw)
    return Path.home() / "Downloads"


# ── Public API (module protocol) ───────────────────────────────────────────


def produces_positions(config: dict[str, object]) -> bool:
    """Fidelity is always on — the ingest path is idempotent and silent on missing CSVs."""
    del config
    return True


def ingest(db_path: Path, config: dict[str, object]) -> None:
    """Scan ``fidelity_downloads`` for ``Accounts_History*.csv`` and ingest each file.

    Files are processed in chronological order by earliest ``MM/DD/YYYY``
    date in their body. Each CSV is authoritative for its own date range, so
    processing oldest→newest naturally deduplicates overlapping exports via
    :func:`parse._ingest_one_csv`'s range-replace.
    """
    downloads_dir = _downloads_dir(config)
    raw_csvs = sorted(downloads_dir.glob("Accounts_History*.csv"))
    raw_csvs.sort(key=parse._csv_earliest_date)
    for csv_path in raw_csvs:
        parse._ingest_one_csv(db_path, csv_path)


def positions_at(
    db_path: Path,
    as_of: date,
    prices: PriceContext,
    config: dict[str, object],
) -> list[PositionRow]:
    """Return one PositionRow per (account, ticker) position + cash bucket.

    Delegates to :func:`etl.replay.replay_transactions` with Fidelity's
    table layout (``run_date`` / ``symbol`` / ``amount`` columns, plus the
    ``account_number`` grouping column) and cash-ledger bookkeeping turned
    on (``track_cash=True``, ``lot_type_col="lot_type"``). MM fund symbols
    are excluded from share accumulation but still flow through cash — the
    ``mm_drip_tickers`` knob credits ``REINVESTMENT`` rows' share counts
    back to the cash ledger the way the legacy replay did.
    """
    from etl.replay import replay_transactions

    result = replay_transactions(
        db_path,
        TABLE,
        as_of,
        date_col="run_date",
        ticker_col="symbol",
        amount_col="amount",
        account_col="account_number",
        exclude_tickers=MM_SYMBOLS,
        track_cash=True,
        lot_type_col="lot_type",
        mm_drip_tickers=MM_SYMBOLS,
    )

    positions = {key: st.quantity for key, st in result.positions.items()}
    cost_basis = {key: st.cost_basis_usd for key, st in result.positions.items()}

    rows = pricing.position_rows(
        positions=positions,
        cost_basis=cost_basis,
        prices=prices,
        mutual_fund_set=pricing.mutual_funds(config),
    )
    rows.extend(cash.cash_rows(result.cash, cash.accounts_map(config)))
    return rows
