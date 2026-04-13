"""Shared fixtures for Gmail triage tests."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FakeEmail:
    msg_id: str
    sender: str
    subject: str
    body_excerpt: str
