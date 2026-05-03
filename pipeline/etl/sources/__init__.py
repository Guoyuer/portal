"""Investment source registry.

Architecture rule: all source-specific logic lives in its own module under
``etl/sources/``. Each source module exposes three free functions —
``ingest(db_path, config)``, ``positions_at(db_path, as_of, prices, config)``,
``produces_positions(config)`` — and this package composes them.

Modules are the identifier (no enum / protocol / class ceremony). The explicit
``SOURCES`` registry drives ``positions_at_all`` (production uses per-source
``.ingest()`` calls directly from ``scripts/build_timemachine_db.py``);
adding a new source is one import plus one list entry here.

Shared types (``ActionKind``, ``PositionRow``, ``PriceContext``,
``InvestmentSource`` Protocol) live in :mod:`etl.sources._types`; concrete
source modules import that file directly, and this package re-exports the
types for public callers.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from etl.types import RawConfig

from . import empower, fidelity, robinhood
from ._types import ActionKind, InvestmentSource, PositionRow, PriceContext

__all__ = [
    "ActionKind",
    "InvestmentSource",
    "PositionRow",
    "PriceContext",
    "SOURCES",
    "positions_at_all",
]


# ── Ordered source list ─────────────────────────────────────────────────────

SOURCES: list[InvestmentSource] = [fidelity, robinhood, empower]


# ── Top-level composition ──────────────────────────────────────────────────


def positions_at_all(
    db_path: Path,
    as_of: date,
    prices: PriceContext,
    config: RawConfig,
) -> list[PositionRow]:
    """Flatten ``positions_at`` across every enabled source."""
    rows: list[PositionRow] = []
    for mod in SOURCES:
        if mod.produces_positions(config):
            rows.extend(mod.positions_at(db_path, as_of, prices, config))
    return rows
