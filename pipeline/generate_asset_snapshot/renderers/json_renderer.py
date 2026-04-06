"""JSON renderer — serializes ReportData via dataclasses.asdict() + camelCase keys.

Zero manual field mapping. TypeScript types mirror this output exactly.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Any

from ..types import ReportData

log = logging.getLogger(__name__)


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


def render(report: ReportData, *, metadata: dict[str, str] | None = None) -> str:
    """Serialize ReportData to JSON with camelCase keys.

    Raw transaction lists are stripped (large, portal doesn't need them).
    Optional metadata (e.g., file timestamps) is included at top level.
    """
    data = asdict(report)
    if metadata:
        data["metadata"] = metadata
    result = json.dumps(_camel_keys(data), indent=2, default=str)
    log.info("JSON rendered: %d chars, %d sections populated", len(result), sum(1 for k, v in data.items() if v is not None))
    return result
