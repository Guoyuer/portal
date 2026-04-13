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
MAX_TOKENS = 4096


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
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=_system_prompt(),
            messages=[{"role": "user", "content": _user_prompt(emails)}],
        )
    except Exception as e:  # noqa: BLE001 — fail-open is the contract
        log.warning("anthropic call failed: %s", e)
        return _fallback(emails, "classifier failed")

    text = response.content[0].text if response.content else ""
    try:
        parsed = json.loads(text)
        items = parsed["classifications"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.warning("anthropic returned unparseable response: %s", e)
        return _fallback(emails, "unparseable response")

    out: dict[str, Classification] = {}
    for item in items:
        try:
            out[item["msg_id"]] = Classification(
                category=Category(item["category"]),
                summary=str(item.get("summary", "")),
            )
        except (KeyError, ValueError) as e:
            log.warning("skipping malformed classification item: %s", e)

    for e in emails:
        out.setdefault(e.msg_id, Classification(Category.NEUTRAL, "no classification returned"))
    return out
