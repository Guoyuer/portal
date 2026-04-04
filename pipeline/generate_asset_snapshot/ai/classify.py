"""Auto-classify unknown tickers using an LLM.

When a new ticker appears in a CSV but is not in config.json,
this module asks an LLM to assign a category and subtype.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ._parse import extract_json

log = logging.getLogger(__name__)

LLMClient = Callable[[str], str]

_SYSTEM_PROMPT = """\
You are a financial instrument classifier.
Given a stock ticker and its description, classify it into ONE of these categories:

Categories: {categories}
Subtypes for equity categories: broad, growth, other
Source is always "fidelity" for individual stocks.

Respond with ONLY a JSON object, no explanation:
{{"category": "...", "subtype": "...", "source": "fidelity"}}
"""


def classify_ticker(
    ticker: str,
    description: str,
    existing_categories: list[str],
    *,
    client: LLMClient | None = None,
) -> dict[str, str] | None:
    """Classify an unknown ticker into a category and subtype.

    Args:
        ticker: Stock ticker symbol (e.g., "AMZN").
        description: Fidelity description (e.g., "AMAZON.COM INC").
        existing_categories: List of category names from config.
        client: LLM client callable. If None, returns None.

    Returns:
        Dict with "category", "subtype", "source" keys, or None.
    """
    if client is None:
        return None

    prompt = _SYSTEM_PROMPT.format(categories=", ".join(existing_categories))
    prompt += f"\n\nTicker: {ticker}\nDescription: {description}"

    try:
        response = client(prompt)
        parsed = extract_json(response, require_key="category")
        if parsed is None:
            log.warning("Could not parse classification for %s: %r", ticker, response)
            return None
        # Validate category against existing list
        if parsed["category"] not in existing_categories:
            log.warning("LLM returned invalid category: %s", parsed)
            return None
        return {
            "category": parsed["category"],
            "subtype": parsed.get("subtype", "other"),
            "source": parsed.get("source", "fidelity"),
        }
    except Exception:
        log.exception("Ticker classification failed for %s", ticker)
        return None
