"""Anthropic classification of Gmail messages.

Fails open: on any Anthropic error or unparseable response, every email
falls back to NEUTRAL with a note so the sync still ships a result for
each email (the UI always shows *something*).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum

from anthropic import Anthropic

from gmail.imap_client import ParsedMessage

log = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 16384  # Haiku 4.5 default max. Only pay for actual output; set high for safety.
BATCH_SIZE = 30     # Per-batch email count. 30 * ~30 tokens/row ≈ 900 tokens output.


class Category(StrEnum):
    IMPORTANT = "IMPORTANT"
    NEUTRAL = "NEUTRAL"
    TRASH_CANDIDATE = "TRASH_CANDIDATE"


@dataclass(frozen=True)
class Classification:
    category: Category
    summary: str


def _system_prompt() -> str:
    return """\
You triage Gmail for the user. Return STRICT JSON only.

Categories:
  IMPORTANT       — recruiter outreach (猎头), emails demanding user action
                    (bills with due dates, security alerts needing response,
                    time-sensitive invitations, emails asking a direct question).
  TRASH_CANDIDATE — promotional newsletters the user doesn't engage with,
                    routine system notifications (login-success, "your weekly
                    summary"), duplicate marketing.
  NEUTRAL         — anything else. When in doubt, NEUTRAL.

Few-shot examples (user's taste calibration):
  "Software Engineer role at Stripe — competitive comp"
    → IMPORTANT (recruiter)
  "Your statement is ready — Chase Freedom"
    → IMPORTANT (bill action)
  "Notion's weekly digest: 5 pages you haven't opened"
    → TRASH_CANDIDATE (marketing)
  "Security alert: sign-in from Chrome on Windows"
    → TRASH_CANDIDATE (routine, own device)
  "Slack: 2 new messages in #general"
    → NEUTRAL
"""


def _user_prompt(emails: list[ParsedMessage]) -> str:
    lines = ["Classify the following emails. Output JSON:"]
    lines.append(
        '  {"classifications": [{"msg_id": "...", '
        '"category": "IMPORTANT|NEUTRAL|TRASH_CANDIDATE", '
        '"summary": "one short sentence"}]}'
    )
    lines.append("")
    lines.append("Emails:")
    for e in emails:
        lines.append(f"[msg_id: {e.msg_id}] From: {e.sender} | Subject: {e.subject}")
        excerpt = e.body_excerpt.replace("\n", " ")[:300]
        if excerpt:
            lines.append(f"  Body: {excerpt}")
    return "\n".join(lines)


def _fallback(emails: list[ParsedMessage], reason: str) -> dict[str, Classification]:
    return {
        e.msg_id: Classification(category=Category.NEUTRAL, summary=f"AI unavailable — {reason}")
        for e in emails
    }


def classify_emails(
    emails: list[ParsedMessage], *, api_key: str,
) -> dict[str, Classification]:
    if not emails:
        return {}

    client = Anthropic(api_key=api_key)
    out: dict[str, Classification] = {}
    for i in range(0, len(emails), BATCH_SIZE):
        _classify_batch(client, emails[i : i + BATCH_SIZE], out)

    # Safety net for any email that didn't land a classification
    for e in emails:
        out.setdefault(e.msg_id, Classification(Category.NEUTRAL, "no classification returned"))
    return out


def _classify_batch(
    client: Anthropic, batch: list[ParsedMessage], out: dict[str, Classification],
) -> None:
    """Classify one batch in-place into ``out``. Per-batch fail-open."""
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=_system_prompt(),
            messages=[{"role": "user", "content": _user_prompt(batch)}],
        )
    except Exception as e:  # noqa: BLE001 — fail-open is the contract
        log.warning("anthropic call failed: %s", e)
        for em in batch:
            out[em.msg_id] = Classification(Category.NEUTRAL, "AI unavailable — classifier failed")
        return

    text = response.content[0].text if response.content else ""
    # Haiku 4.5 commonly wraps JSON in ```json ... ``` fences. Strip them.
    stripped = text.strip()
    if stripped.startswith("```"):
        first_nl = stripped.find("\n")
        last_fence = stripped.rfind("```")
        if first_nl != -1 and last_fence > first_nl:
            stripped = stripped[first_nl + 1 : last_fence].strip()
    try:
        parsed = json.loads(stripped)
        items = parsed["classifications"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.warning("batch unparseable: %s (raw=%r)", e, text[:200])
        for em in batch:
            out[em.msg_id] = Classification(Category.NEUTRAL, "AI unavailable — unparseable response")
        return

    # Model often strips RFC 5322 angle brackets on the way out.
    # Map both <x@y> and x@y to the canonical msg_id we sent.
    canonical = {e.msg_id.strip("<>"): e.msg_id for e in batch}
    for item in items:
        try:
            returned = str(item["msg_id"])
            actual_id = canonical.get(returned.strip("<>"), returned)
            out[actual_id] = Classification(
                category=Category(item["category"]),
                summary=str(item.get("summary", "")),
            )
        except (KeyError, ValueError) as e:
            log.warning("skipping malformed classification item: %s", e)
