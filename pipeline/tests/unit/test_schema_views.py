"""Tests for the _VIEWS dictionary and MARKET_META_KEYS — generator contract.

These pin down that:
  1. db.py owns all view DDL (no hand-written SQL in worker/schema.sql).
  2. v_market_meta's column list is derived from MARKET_META_KEYS — adding a
     FRED indicator is a single-site edit.
"""

from __future__ import annotations

import re

import pytest


def test_views_is_non_empty_dict() -> None:
    from generate_asset_snapshot.db import _VIEWS

    assert isinstance(_VIEWS, dict)
    assert len(_VIEWS) > 0


def test_every_view_name_starts_with_v_prefix() -> None:
    from generate_asset_snapshot.db import _VIEWS

    for name in _VIEWS:
        assert name.startswith("v_"), f"view name must start with 'v_': {name}"


def test_every_view_sql_contains_from() -> None:
    from generate_asset_snapshot.db import _VIEWS

    for name, sql in _VIEWS.items():
        assert re.search(r"\bFROM\b", sql, re.IGNORECASE), f"view {name} missing FROM"


def test_every_view_sql_contains_create_view_if_not_exists() -> None:
    from generate_asset_snapshot.db import _VIEWS

    for name, sql in _VIEWS.items():
        # Each DDL statement must use CREATE VIEW IF NOT EXISTS and reference its own name.
        assert re.search(rf"CREATE\s+VIEW\s+IF\s+NOT\s+EXISTS\s+{name}\b", sql, re.IGNORECASE), (
            f"view {name} SQL does not start with its matching CREATE VIEW"
        )


def test_required_views_present() -> None:
    from generate_asset_snapshot.db import _VIEWS

    required = {
        "v_daily",
        "v_daily_tickers",
        "v_fidelity_txns",
        "v_qianji_txns",
        "v_market_indices",
        "v_market_indicators",
        "v_market_meta",
        "v_holdings_detail",
        "v_econ_series",
        "v_econ_snapshot",
    }
    assert required.issubset(set(_VIEWS.keys()))


def test_market_meta_keys_is_non_empty_list() -> None:
    from generate_asset_snapshot.types import MARKET_META_KEYS

    assert isinstance(MARKET_META_KEYS, list)
    assert len(MARKET_META_KEYS) > 0
    assert all(isinstance(k, str) and k for k in MARKET_META_KEYS)


def test_v_market_meta_contains_every_market_meta_key() -> None:
    """v_market_meta SQL must reference every key in MARKET_META_KEYS, and no other pivot keys."""
    from generate_asset_snapshot.db import _VIEWS
    from generate_asset_snapshot.types import MARKET_META_KEYS

    sql = _VIEWS["v_market_meta"]
    # Extract pivot keys from CASE WHEN clauses
    pivoted = set(re.findall(r"key\s*=\s*'([A-Za-z0-9_]+)'", sql))
    assert pivoted == set(MARKET_META_KEYS), (
        f"v_market_meta pivots on {pivoted} but MARKET_META_KEYS is {set(MARKET_META_KEYS)}"
    )


def test_v_market_meta_sql_aliases_each_key() -> None:
    """Each MARKET_META_KEYS entry must appear as an ``AS <key>`` alias in v_market_meta."""
    from generate_asset_snapshot.db import _VIEWS
    from generate_asset_snapshot.types import MARKET_META_KEYS

    sql = _VIEWS["v_market_meta"]
    for key in MARKET_META_KEYS:
        assert re.search(rf"AS\s+{key}\b", sql), f"v_market_meta missing alias '{key}'"


def test_fred_snapshot_keys_subset_of_market_meta_keys() -> None:
    """Every key emitted by _precompute_fred must have a pivot column in v_market_meta."""
    from generate_asset_snapshot.precompute import _FRED_SNAPSHOT_KEYS
    from generate_asset_snapshot.types import MARKET_META_KEYS

    fred_dst_keys = set(_FRED_SNAPSHOT_KEYS.values())
    mm_keys = set(MARKET_META_KEYS)
    missing = fred_dst_keys - mm_keys
    assert not missing, f"FRED snapshot keys not pivoted by v_market_meta: {missing}"


def test_usd_cny_in_market_meta_keys() -> None:
    """usdCny comes from _precompute_cny (not FRED) — must still be in the pivot."""
    from generate_asset_snapshot.types import MARKET_META_KEYS

    assert "usdCny" in MARKET_META_KEYS


def test_init_db_creates_all_views() -> None:
    """After init_db, sqlite_master lists every view in _VIEWS."""
    import sqlite3
    import tempfile
    from pathlib import Path

    from generate_asset_snapshot.db import _VIEWS, init_db

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
    from generate_asset_snapshot.db import _VIEWS

    for name in _VIEWS:
        assert f"CREATE VIEW IF NOT EXISTS {name}" in text, (
            f"worker/schema.sql missing view {name}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
