"""Orchestration sub-package for the Portal sync pipeline.

Split from the original monolithic ``scripts/run_automation.py`` so each concern
(paths, change detection, notification, state machine) lives in its own module
and is independently testable. The outer script reduces to a thin
``argparse → Runner`` entry point.

Public surface:
    * :class:`Runner` — the detect → build → verify → publish state machine.
    * :func:`parse_args` — CLI shared between the script and tests.
    * Exit-code constants (``EXIT_OK``, ``EXIT_BUILD_FAIL``, ...).

Sub-modules are re-exported here so external callers can
``from etl.automation import Runner, parse_args`` without caring about the
internal layout. Helpers specific to paths / change-detection / notification
live in their respective submodules and are imported directly when needed.
"""
from __future__ import annotations

from ._constants import (
    EXIT_BUILD_FAIL,
    EXIT_OK,
    EXIT_PARITY_FAIL,
    EXIT_POSITIONS_FAIL,
    EXIT_SYNC_FAIL,
)
from .runner import Runner, parse_args

__all__ = [
    "EXIT_BUILD_FAIL",
    "EXIT_OK",
    "EXIT_PARITY_FAIL",
    "EXIT_POSITIONS_FAIL",
    "EXIT_SYNC_FAIL",
    "Runner",
    "parse_args",
]
