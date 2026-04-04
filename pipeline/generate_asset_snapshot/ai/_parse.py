"""Shared JSON extraction helper for LLM responses."""

from __future__ import annotations

import json
import re
from typing import Any


def extract_json(text: str, *, require_key: str | None = None) -> dict[str, Any] | None:
    """Try to extract a JSON object from *text*, handling prose and code blocks.

    Parameters
    ----------
    require_key
        If set, the parsed dict must contain this key to be accepted.
    """
    stripped = text.strip()

    # Handle explicit "null" / "none" responses.
    if stripped.lower() in ("null", "none"):
        return None

    # Strip markdown code blocks.
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    # Try direct parse.
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict) and (require_key is None or require_key in obj):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass

    # Fall back: find first {...} in the text.
    match = re.search(r"\{[^}]+\}", text)
    if match:
        try:
            obj = json.loads(match.group())
            if isinstance(obj, dict) and (require_key is None or require_key in obj):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

    return None
