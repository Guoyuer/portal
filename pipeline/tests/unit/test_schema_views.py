"""Tests for the _VIEWS dictionary — generator contract.

Pins down that db.py owns all view DDL (no hand-written SQL in
worker/schema.sql).
"""

from __future__ import annotations

import re

import pytest


def test_views_is_non_empty_dict() -> None:
    from etl.db import _VIEWS

    assert isinstance(_VIEWS, dict)
    assert len(_VIEWS) > 0


def test_every_view_name_starts_with_v_prefix() -> None:
    from etl.db import _VIEWS

    for name in _VIEWS:
        assert name.startswith("v_"), f"view name must start with 'v_': {name}"


def test_every_view_sql_contains_from() -> None:
    from etl.db import _VIEWS

    for name, sql in _VIEWS.items():
        assert re.search(r"\bFROM\b", sql, re.IGNORECASE), f"view {name} missing FROM"


def test_every_view_sql_contains_create_view_if_not_exists() -> None:
    from etl.db import _VIEWS

    for name, sql in _VIEWS.items():
        # Each DDL statement must use CREATE VIEW IF NOT EXISTS and reference its own name.
        assert re.search(rf"CREATE\s+VIEW\s+IF\s+NOT\s+EXISTS\s+{name}\b", sql, re.IGNORECASE), (
            f"view {name} SQL does not start with its matching CREATE VIEW"
        )


def test_required_views_present() -> None:
    from etl.db import _VIEWS

    required = {
        "v_daily",
        "v_daily_tickers",
        "v_fidelity_txns",
        "v_qianji_txns",
        "v_market_indices",
        "v_holdings_detail",
        "v_econ_series",
        "v_econ_snapshot",
    }
    assert required.issubset(set(_VIEWS.keys()))


def test_init_db_creates_all_views() -> None:
    """After init_db, sqlite_master lists every view in _VIEWS."""
    import sqlite3
    import tempfile
    from pathlib import Path

    from etl.db import _VIEWS, init_db

    tmp = Path(tempfile.mktemp(suffix=".db"))
    try:
        init_db(tmp)
        conn = sqlite3.connect(tmp)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            ).fetchall()
        finally:
            conn.close()
        view_names = {r[0] for r in rows}
        assert set(_VIEWS.keys()).issubset(view_names), (
            f"init_db did not create: {set(_VIEWS.keys()) - view_names}"
        )
    finally:
        tmp.unlink(missing_ok=True)


def test_gen_schema_sql_output_contains_every_view() -> None:
    """After running gen_schema_sql, worker/schema.sql contains every view name."""
    from pathlib import Path

    schema_path = Path(__file__).resolve().parents[3] / "worker" / "schema.sql"
    text = schema_path.read_text(encoding="utf-8")
    from etl.db import _VIEWS

    for name in _VIEWS:
        assert f"CREATE VIEW IF NOT EXISTS {name}" in text, (
            f"worker/schema.sql missing view {name}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
