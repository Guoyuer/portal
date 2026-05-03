"""Investment source registry.

Architecture rule: all source-specific logic lives in its own module under
``etl/sources/``. Each source module exposes three free functions —
``ingest(db_path, config)``, ``positions_at(db_path, as_of, prices, config)``,
``produces_positions(config)`` — and this package composes them.

Modules are the identifier (no enum / protocol / class ceremony). The lazy
``SOURCES`` registry drives ``positions_at_all`` (production uses per-source
``.ingest()`` calls directly from ``scripts/build_timemachine_db.py``);
adding a new source is one import line here.

Shared types (``ActionKind``, ``PositionRow``, ``PriceContext``,
``InvestmentSource`` Protocol) live in :mod:`etl.sources._types` so consumers
can import them without triggering the full registry load.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from etl.types import RawConfig

from ._types import ActionKind, InvestmentSource, PositionRow, PriceContext

__all__ = [
    "ActionKind",
    "InvestmentSource",
    "PositionRow",
    "PriceContext",
    "SOURCES",
    "positions_at_all",
]

if TYPE_CHECKING:
    SOURCES: list[ModuleType]


# ── Ordered source list ─────────────────────────────────────────────────────

_SOURCE_CACHE: list[ModuleType] | None = None


def _sources() -> list[ModuleType]:
    from . import empower, fidelity, robinhood
    return [fidelity, robinhood, empower]


def _get_sources() -> list[ModuleType]:
    global _SOURCE_CACHE
    if _SOURCE_CACHE is None:
        _SOURCE_CACHE = _sources()
    return _SOURCE_CACHE


def __getattr__(name: str) -> object:
    if name == "SOURCES":
        return _get_sources()
    raise AttributeError(name)


# ── Top-level composition ──────────────────────────────────────────────────


def positions_at_all(
    db_path: Path,
    as_of: date,
    prices: PriceContext,
    config: RawConfig,
) -> list[PositionRow]:
    """Flatten ``positions_at`` across every enabled source."""
    rows: list[PositionRow] = []
    for mod in _get_sources():
        if mod.produces_positions(config):
            rows.extend(mod.positions_at(db_path, as_of, prices, config))
    return rows
