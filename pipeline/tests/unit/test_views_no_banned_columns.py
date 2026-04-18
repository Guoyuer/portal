"""Contract test: D1 views must NEVER project banned columns.

Local SQLite and D1 share one schema (every local column is mirrored to D1),
so the payload-exposure contract lives entirely in the views. The Worker
serves ``GET /timeline`` as ``SELECT * FROM v_<name>``, meaning whatever
columns a view projects land verbatim in the JSON payload. This test
enforces the boundary by pattern-matching the raw ``CREATE VIEW`` SQL for
identifiers that should **never** reach the frontend.

Banned columns fall into two groups:

    PII-ish / noisy raw data with no frontend consumer:
      fidelity: account_number, action
      qianji: note

    Local-compute intermediates:
      fidelity: action_kind, lot_type

This is the sole gate for payload exposure — strictly stronger than a
sync-time whitelist would have been, because a view leaking a banned
column would propagate regardless of what sync did.

Coverage limitation: this test checks views only. A Worker handler that
runs ``SELECT * FROM <raw_table>`` directly would also leak. The only
direct-table queries today (``worker/src/index.ts``) use explicit column
lists — enforce that invariant in code review.
"""
from __future__ import annotations

import re

from etl.db import _VIEWS

# Identifiers that must never appear in any view body (case-insensitive,
# word-bounded). Adding to this list is how you declare a column
# local-only without splitting the shared schema into independent
# local/D1 halves.
_BANNED_IDENTIFIERS: tuple[str, ...] = (
    "account_number",
    "action",
    "action_kind",
    "lot_type",
    "note",
)


def _strip_sql_comments(sql: str) -> str:
    """Drop ``-- line comments`` and ``/* block comments */``. Keeps identifier
    matching out of comment false-positives (e.g. a docstring mentioning
    ``account``)."""
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return sql


def test_no_view_projects_banned_identifier() -> None:
    """For every banned identifier, assert no view body references it."""
    violations: list[str] = []
    for view_name, sql in _VIEWS.items():
        body = _strip_sql_comments(sql)
        for banned in _BANNED_IDENTIFIERS:
            # \b ensures "account" doesn't match "account_number" or vice versa;
            # re.IGNORECASE covers any mixed-case alias forms.
            if re.search(rf"\b{re.escape(banned)}\b", body, flags=re.IGNORECASE):
                violations.append(f"{view_name} references banned column `{banned}`")
    assert not violations, (
        "Views must not expose these columns to the frontend payload:\n  "
        + "\n  ".join(violations)
    )


def test_banned_list_covers_local_only_columns() -> None:
    """Smoke test on the banned list itself — every column in the local
    schema that has no frontend consumer must stay on the list. Shrinking
    it is an intentional exposure decision; flag it so review catches."""
    expected_at_minimum = {
        "account_number",  # replay grouping key
        "action",          # action_kind resync input
        "action_kind",     # replay classification
        "lot_type",        # replay lot-type bookkeeping
        "note",            # email low-count expand
    }
    assert set(_BANNED_IDENTIFIERS) >= expected_at_minimum, (
        "Banned identifier list shrunk below the local-only baseline. If "
        "that's intentional (column is being exposed to the frontend), "
        "update this test; otherwise restore the missing identifiers."
    )
