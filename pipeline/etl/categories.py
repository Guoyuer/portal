"""Category metadata ingestion: config.json → categories table.

The 4 asset categories have stable camelCase keys that match DailyPoint's
columns (usEquity, nonUsEquity, crypto, safeNet). config.json uses display
names; this module bridges the two.
"""

from __future__ import annotations

from pathlib import Path

from .db import get_connection
from .types import RawConfig

# ── Public API ──────────────────────────────────────────────────────────────

# Display-name → camelCase key (matches DailyPoint fields in src/lib/schema.ts).
CATEGORY_NAME_TO_KEY: dict[str, str] = {
    "US Equity": "usEquity",
    "Non-US Equity": "nonUsEquity",
    "Crypto": "crypto",
    "Safe Net": "safeNet",
}


def ingest_categories(db_path: Path, config: RawConfig) -> int:
    """Replace the categories table from config.json's target_weights + order.

    Names not mapped in :data:`CATEGORY_NAME_TO_KEY` are skipped (not an
    error — the pipeline is forward-compatible with future categories that
    lack a frontend key yet). Returns rows written.
    """
    weights = config.get("target_weights", {})
    order = config.get("category_order") or list(weights.keys())

    rows: list[tuple[str, str, int, float]] = []
    display_order = 0
    for name in order:
        key = CATEGORY_NAME_TO_KEY.get(name)
        if key is None:
            continue
        target = float(weights.get(name, 0.0) or 0.0)
        rows.append((key, name, display_order, target))
        display_order += 1

    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM categories")
        if rows:
            conn.executemany(
                "INSERT INTO categories (key, name, display_order, target_pct)"
                " VALUES (?, ?, ?, ?)",
                rows,
            )
        conn.commit()
    finally:
        conn.close()
    return len(rows)
