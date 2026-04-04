"""Default LLM client using the Anthropic Messages API.

The ``default_client`` function is used when callers don't inject their own
client.  It reads ``ANTHROPIC_API_KEY`` from the environment and sends a
single-turn request to Claude Haiku for cost efficiency.

This module is intentionally separated so that the rest of the AI layer has
zero import-time dependency on ``anthropic`` (the SDK).
"""

from __future__ import annotations

import json
import os
import urllib.request

_API_URL = "https://api.anthropic.com/v1/messages"
_DEFAULT_MODEL = "claude-haiku-4-20250414"
_MAX_TOKENS = 1024


def default_client(prompt: str) -> str:
    """Call the Anthropic Messages API with *prompt* and return the text.

    Uses stdlib ``urllib`` so there is no hard dependency on the
    ``anthropic`` Python SDK — keeps the package lightweight.

    Raises on HTTP/network errors (callers are expected to catch).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise OSError("ANTHROPIC_API_KEY environment variable is not set")

    payload = json.dumps(
        {
            "model": _DEFAULT_MODEL,
            "max_tokens": _MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()

    req = urllib.request.Request(
        _API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode())

    # Extract text from the first content block.
    return str(body["content"][0]["text"])
