"""Fidelity source — composes the ``parse`` / ``cash`` / ``pricing`` submodules.

Public surface mirrors :mod:`etl.sources.robinhood` / :mod:`etl.sources.empower`:
``produces_positions(config)``, ``ingest(db_path, config)``,
``positions_at(db_path, as_of, prices, config)``.

``positions_at`` delegates transaction replay to the legacy
:func:`etl.timemachine.replay_from_db`. The source-agnostic
:func:`etl.replay.replay_transactions` primitive understands a narrower
action alphabet (BUY / SELL / REINVESTMENT only) than Fidelity's transaction
stream, which also includes REDEMPTION PAYOUT, TRANSFERRED FROM/TO,
DISTRIBUTION, and EXCHANGED TO — all position-affecting actions that
``_replay_core`` handles via ``POSITION_PREFIXES``. Switching Fidelity to
the narrower primitive would change the share-count output for real data;
the migration to that primitive is a separate, behaviour-preserving refactor.
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

__all__ = [
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

    Reuses :func:`etl.timemachine.replay_from_db` for the core cost-basis
    accumulator. That function understands the full Fidelity action alphabet
    (BUY / SELL / REINVESTMENT plus REDEMPTION PAYOUT, TRANSFERRED FROM/TO,
    DISTRIBUTION, EXCHANGED TO) and correctly excludes money-market symbols
    from position accumulation. The narrower
    :func:`etl.replay.replay_transactions` primitive is not yet sufficient;
    migrating to it is a separate refactor.
    """
    from etl.timemachine import replay_from_db

    result = replay_from_db(db_path, as_of)
    rows = pricing.position_rows(
        positions=result["positions"],
        cost_basis=result.get("cost_basis") or {},
        prices=prices,
        mutual_fund_set=pricing.mutual_funds(config),
    )
    rows.extend(cash.cash_rows(result["cash"], cash.accounts_map(config)))
    return rows
