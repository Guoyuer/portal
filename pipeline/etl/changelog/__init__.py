"""Sync changelog — DB snapshot, diff, and email body rendering.

Split into three cohesive submodules:
  * :mod:`snapshot` — :class:`NetWorthPoint`, :class:`SyncSnapshot`,
    :func:`capture` (read local DB), :class:`SyncChangelog`, :func:`diff`.
  * :mod:`categorize` — category-specific aggregation helpers: money/qty
    formatting, ``_LARGE_MOVE_*`` thresholds, the component-sum drift check,
    and the exit-code → gate-name mapping.
  * :mod:`render` — per-section ``_render_*`` helpers + :func:`format_text` /
    :func:`format_html` / :func:`build_subject` (email body templating).

Public API is re-exported from this package; external callers continue to
``from etl.changelog import ...`` without caring about the submodule layout.
"""
from __future__ import annotations

from .render import build_subject, format_html, format_text
from .snapshot import NetWorthPoint, SyncChangelog, SyncSnapshot, capture, diff

__all__ = [
    "NetWorthPoint",
    "SyncChangelog",
    "SyncSnapshot",
    "build_subject",
    "capture",
    "diff",
    "format_html",
    "format_text",
]
