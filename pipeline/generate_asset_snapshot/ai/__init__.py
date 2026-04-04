"""AI integration layer — LLM-powered classification, narrative, and NLU.

All functions accept an optional ``client`` callable ``(prompt -> text)`` for
dependency injection.  When ``client`` is ``None``:
- narrative returns ``None`` (AI features are optional)
- classify returns ``None`` (unknown ticker stays unclassified)
- parse_command tries regex first, returns ``None`` if no match

To use a real LLM, pass a client that calls the Anthropic Messages API
(see ``_client.default_client`` for a reference implementation).
"""

from __future__ import annotations

from .classify import classify_ticker as classify_ticker
from .narrative import generate_narrative as generate_narrative
from .nlu import parse_command as parse_command

__all__ = ["classify_ticker", "generate_narrative", "parse_command"]
