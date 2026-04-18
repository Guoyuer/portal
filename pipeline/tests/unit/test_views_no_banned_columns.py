"""Contract test: D1 views must NEVER project banned columns.

The Worker serves ``GET /timeline`` as ``SELECT * FROM v_<name>`` for every
view in ``worker/src/index.ts``, so whatever columns the views project lands
in the JSON payload. This test enforces the payload boundary by pattern-
matching the raw ``CREATE VIEW`` SQL for identifiers that should **never**
reach the frontend.

Two categories of banned columns:

    PII-ish / noisy source data that has no frontend consumer:
      fidelity: account, account_number, description, lot_type, settlement_date
      qianji: account, note

    Local-only pipeline internals (cached classifications):
      fidelity: action_kind

This is the payload-layer replacement for the old ``_D1_OMITTED`` whitelist
in ``sync_to_d1.py``. The old defence lived at the sync stage; the new one
lives at the view stage — which is strictly stronger, because a view that
projects a banned column would leak it regardless of how syncing worked.

Coverage limitation: this test checks views only. A Worker handler that
runs ``SELECT * FROM <raw_table>`` directly would also leak. The only such
direct-table queries today (``worker/src/index.ts``) use explicit column
lists — enforce that invariant in code review.
"""
from __future__ import annotations

import re

from etl.db import _VIEWS

# Identifiers that must never appear in any view body (case-insensitive, word-bounded).
# Adding to this list is the new "opt-out" mechanism — strictly stronger than
# _D1_OMITTED because it's checked at the payload gate, not the sync stage.
_BANNED_IDENTIFIERS: tuple[str, ...] = (
    "account",
    "account_number",
    "description",
    "lot_type",
    "settlement_date",
    "note",
    "action_kind",
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


def test_banned_list_covers_historically_omitted_columns() -> None:
    """Smoke test on the banned list itself — if someone shrinks it below
    what ``_D1_OMITTED`` covered, flag that as an intentional decision, not
    an accident."""
    expected_at_minimum = {
        "account",
        "account_number",
        "description",
        "lot_type",
        "settlement_date",
        "note",
        "action_kind",
    }
    assert set(_BANNED_IDENTIFIERS) >= expected_at_minimum, (
        "Banned identifier list shrunk below the _D1_OMITTED baseline. "
        "If that's intentional, update this test; otherwise restore the "
        "missing identifiers."
    )
