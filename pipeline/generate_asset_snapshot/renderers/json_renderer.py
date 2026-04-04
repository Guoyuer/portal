"""JSON renderer — serializes ReportData via dataclasses.asdict() + camelCase keys.

Zero manual field mapping. TypeScript types mirror this output exactly.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from ..types import ReportData


def _to_camel(s: str) -> str:
    """Convert snake_case to camelCase."""
    parts = s.split("_")
    return parts[0] + "".join(w.capitalize() for w in parts[1:])


def _camel_keys(obj: Any) -> Any:
    """Recursively convert all dict keys from snake_case to camelCase."""
    if isinstance(obj, dict):
        return {_to_camel(k): _camel_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_camel_keys(i) for i in obj]
    return obj


def render(report: ReportData) -> str:
    """Serialize ReportData to JSON with camelCase keys.

    Raw transaction lists are stripped (large, portal doesn't need them).
    """
    data = asdict(report)
    # Strip raw transaction lists — portal uses pre-computed aggregations
    if data.get("activity"):
        for key in ("deposits", "withdrawals", "buys", "sells", "dividends"):
            data["activity"].pop(key, None)
    return json.dumps(_camel_keys(data), indent=2, default=str)
