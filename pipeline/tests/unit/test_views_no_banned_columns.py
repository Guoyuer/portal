"""Contract test: D1 views must not project PII columns.

Local SQLite and D1 share one schema (every local column is mirrored to
D1), so the payload-exposure contract lives entirely in the views. The
Worker serves ``GET /timeline`` as ``SELECT * FROM v_<name>``, meaning
whatever columns a view projects land verbatim in the JSON payload.

This test enforces a single explicit boundary: ``qianji.note`` is user
free-text that may contain PII and must never appear in any view.

Keeping other columns out of views (fidelity.action_kind, .lot_type,
.account_number, etc.) is the SELECT list author's responsibility — omit
them by not writing them. Those aren't privacy-critical; they're
implementation details whose non-exposure is enforced by ordinary review,
not by this test.

Coverage limitation: views only. A Worker handler that runs
``SELECT * FROM <raw_table>`` directly would leak ``note``. The only
direct-table queries today (``worker/src/index.ts``) use explicit column
lists — enforce that invariant in code review.
"""
from __future__ import annotations

import re

from etl.db import _VIEWS

# Qianji ``note`` is user free-text that may contain PII (personal
# details, sensitive comments). This is the only column where leaking
# into the payload is a genuine privacy concern; other local-only
# columns (fidelity.action_kind, .lot_type, .account_number, etc.) are
# kept out of views by omission in the SELECT list, not by this test.
_BANNED_IDENTIFIERS: tuple[str, ...] = ("note",)


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


