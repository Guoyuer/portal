"""Fidelity source — composes the ``parse`` / ``cash`` / ``pricing`` submodules.

Public surface mirrors :mod:`etl.sources.robinhood` / :mod:`etl.sources.empower`:
``produces_positions(config)``, ``ingest(db_path, config)``,
``positions_at(db_path, as_of, prices, config)``.

``positions_at`` delegates transaction replay to the source-agnostic
:func:`etl.replay.replay_transactions` primitive via ``FIDELITY_REPLAY``
(see :class:`etl.replay.ReplayConfig`). The primitive covers Fidelity's
full action vocabulary — BUY / SELL / REINVESTMENT plus the qty-only
kinds REDEMPTION / DISTRIBUTION / EXCHANGE / TRANSFER (``REDEMPTION
PAYOUT``, ``TRANSFERRED FROM/TO``, ``DISTRIBUTION``, ``EXCHANGED TO``)
— and ``exclude_tickers`` filters MM fund symbols out of share
accumulation while still letting them flow through the cash ledger.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from etl.replay import ReplayConfig, replay_transactions
from etl.sources._types import PositionRow, PriceContext

from . import cash, parse, pricing
from .parse import TABLE, classify_fidelity_action

# Fidelity-specific money-market fund tickers. Treated as $1/share cash, so
# they stay out of the per-share position accumulator and instead flow
# through the cash ledger (and, for REINVESTMENT rows, into the MM DRIP
# adjustment that corrects for shares credited without a paired cash entry).
MM_SYMBOLS: frozenset[str] = frozenset({"SPAXX", "FZFXX", "FDRXX"})

# Per-source replay config — passed to :func:`etl.replay.replay_transactions`.
FIDELITY_REPLAY = ReplayConfig(
    table=TABLE,
    date_col="run_date",
    ticker_col="symbol",
    amount_col="amount",
    account_col="account_number",
    exclude_tickers=MM_SYMBOLS,
    track_cash=True,
    lot_type_col="lot_type",
    mm_drip_tickers=MM_SYMBOLS,
)

__all__ = [
    "FIDELITY_REPLAY",
    "MM_SYMBOLS",
    "TABLE",
    "classify_fidelity_action",
    "ingest",
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

    Delegates to :func:`etl.replay.replay_transactions` with the module-
    level :data:`FIDELITY_REPLAY` config (schema + cash-ledger knobs).
    MM fund symbols are excluded from share accumulation but still flow
    through cash — the ``mm_drip_tickers`` knob credits ``REINVESTMENT``
    rows' share counts back to the cash ledger the way the legacy replay
    did.
    """
    result = replay_transactions(db_path, FIDELITY_REPLAY, as_of)

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
