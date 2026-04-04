"""Parse natural language email commands into structured actions.

Supports both English and Chinese commands. Falls back to regex
patterns before calling an LLM, so common patterns are free and fast.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from ._parse import extract_json

log = logging.getLogger(__name__)

LLMClient = Callable[[str], str]

# ── Regex-based fast path (no LLM needed) ────────────────────────────────────

_CONTRIBUTE_PATTERNS = [
    re.compile(r"(?:invest|contribute|allocate|put\s+in)\s+\$?([\d,]+)", re.IGNORECASE),
    re.compile(r"投\s*([\d,]+)"),
    re.compile(r"分配\s*([\d,]+)"),
]

_UPDATE_PATTERNS = [
    re.compile(r"(.+?)\s*(?:现在值|now worth|is now|updated? to)\s*\$?([\d,.]+)", re.IGNORECASE),
]

_QUERY_PATTERNS = [
    re.compile(r"(?:show|list|查看|看)\s+(?:my\s+)?(.+)", re.IGNORECASE),
]


def _try_regex(text: str) -> dict[str, Any] | None:
    """Try regex patterns first — free and deterministic."""
    for pat in _CONTRIBUTE_PATTERNS:
        m = pat.search(text)
        if m:
            return {"action": "contribute", "amount": float(m.group(1).replace(",", ""))}

    for pat in _UPDATE_PATTERNS:
        m = pat.search(text)
        if m:
            return {"action": "update_manual", "asset": m.group(1).strip(), "value": float(m.group(2).replace(",", ""))}

    for pat in _QUERY_PATTERNS:
        m = pat.search(text)
        if m:
            return {"action": "query", "subject": m.group(1).strip()}

    return None


# ── LLM-based fallback ───────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a command parser for a personal finance tool.
Parse the user's natural language into a structured command.

Supported actions:
- contribute: user wants to simulate investing an amount
- update_manual: user wants to update a manual asset value
- query: user wants to see specific data
- config: user wants to change a configuration
- abbreviate: user wants a shorter report

Respond with ONLY a JSON object:
{"action": "...", ...relevant_fields}

If the text is not a command (just a greeting, question, etc.), respond with:
{"action": null}
"""


def parse_command(text: str, *, client: LLMClient | None = None) -> dict[str, Any] | None:
    """Parse a natural language command into a structured action.

    Tries fast regex patterns first, falls back to LLM if available.

    Args:
        text: The email body text.
        client: LLM client callable. If None, only regex patterns are used.

    Returns:
        Dict with "action" key and action-specific fields, or None.
    """
    if not text or not text.strip():
        return None

    # Fast path: regex
    result = _try_regex(text)
    if result:
        return result

    # Slow path: LLM
    if client is None:
        return None

    try:
        response = client(_SYSTEM_PROMPT + "\n\nUser message: " + text)
        parsed = extract_json(response)
        if parsed is None or parsed.get("action") is None:
            return None
        return parsed
    except Exception:
        log.exception("NLU parsing failed for: %s", text[:100])
        return None
