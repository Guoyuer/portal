"""Generate worker/schema.sql DDL from db.py — tables, indexes, and views.

Local SQLite and D1 share a single schema: ``etl/db.py`` is the source of
truth, and the generator mirrors every synced table / index / view into
``worker/schema.sql`` verbatim. No hand-edits are preserved.

The payload-exposure contract (what reaches the frontend) lives in the
views, not in a separate column-subset whitelist. ``test_views_no_banned_columns``
guards identifiers that must never appear in a view body.

Usage:
    cd pipeline && python scripts/gen_schema_sql.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Allow importing from the pipeline package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl.db import _INDEXES, _TABLES, _VIEWS  # noqa: E402, I001
from scripts.sync_to_d1 import TABLES_TO_SYNC  # noqa: E402, I001

# ── Configuration ──────────────────────────────────────────────────────────────

_SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "worker" / "schema.sql"

_HEADER = """\
-- GENERATED FILE — DO NOT EDIT.
-- Source of truth: pipeline/etl/db.py (_TABLES, _INDEXES, _VIEWS)
-- Regenerate:      cd pipeline && python3 scripts/gen_schema_sql.py
-- (Views expose camelCase column names matching the TypeScript type contract.)
"""

# ── Helpers ────────────────────────────────────────────────────────────────────


def _parse_create_blocks(ddl: str, keyword: str) -> list[tuple[str, str]]:
    """Split a DDL string into (object_name, full_statement) pairs.

    keyword should be 'TABLE' or 'INDEX'.
    """
    # Match CREATE TABLE/INDEX ... (...); or CREATE INDEX ... ON ...;
    pattern = rf"(CREATE\s+{keyword}\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*(?:\([\s\S]*?\)|ON\s+\w+\([^)]*\))\s*;)"
    return [(m.group(2), m.group(1)) for m in re.finditer(pattern, ddl)]


def _table_for_index(create_idx: str) -> str | None:
    """Extract the table name from a CREATE INDEX statement."""
    m = re.search(r"ON\s+(\w+)\s*\(", create_idx)
    return m.group(1) if m else None


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    synced = set(TABLES_TO_SYNC)

    # Parse and filter tables — keep every column from the local schema,
    # since local and D1 share one shape.
    tables = _parse_create_blocks(_TABLES, "TABLE")
    kept_tables = [(name, stmt) for name, stmt in tables if name in synced]

    # Parse and filter indexes (keep only those on synced tables).
    indexes = _parse_create_blocks(_INDEXES, "INDEX")
    kept_indexes = [
        (name, stmt) for name, stmt in indexes if _table_for_index(stmt) in synced
    ]

    # Assemble output
    parts: list[str] = [_HEADER]

    parts.append("-- ── Tables ────────────────────────────────────────────────────────────────────\n")
    for _name, stmt in kept_tables:
        parts.append(stmt)
        parts.append("")

    parts.append("-- ── Indexes ───────────────────────────────────────────────────────────────────\n")
    for _name, stmt in kept_indexes:
        parts.append(stmt)
    parts.append("")

    # sync_meta is D1-only (not in local DB) — always include
    parts.append("-- Sync metadata (last_sync timestamp, data coverage)")
    parts.append("CREATE TABLE IF NOT EXISTS sync_meta (")
    parts.append("    key   TEXT PRIMARY KEY,")
    parts.append("    value TEXT NOT NULL")
    parts.append(");")
    parts.append("")

    parts.append("-- ── camelCase views (match TypeScript type contract) ──────────────────────────")
    parts.append("-- Views use DROP + CREATE to make schema application idempotent — re-running")
    parts.append("-- wrangler d1 execute --file=schema.sql picks up definition changes.")
    parts.append("")
    for name, view_sql in _VIEWS.items():
        parts.append(f"DROP VIEW IF EXISTS {name};")
        parts.append(view_sql)
        parts.append("")

    output = "\n".join(parts).rstrip() + "\n"
    _SCHEMA_PATH.write_text(output, encoding="utf-8")
    print(
        f"Wrote {_SCHEMA_PATH} ({len(kept_tables)} tables, "
        f"{len(kept_indexes)} indexes, {len(_VIEWS)} views)"
    )


if __name__ == "__main__":
    main()
