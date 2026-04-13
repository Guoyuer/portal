"""Tests for Anthropic classification call and response parsing."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from gmail.classify import Category, classify_emails
from gmail.imap_client import ParsedMessage


def _msg(msg_id: str, subject: str) -> ParsedMessage:
    return ParsedMessage(
        msg_id=msg_id, received_at="2026-04-12T10:00:00+00:00",
        sender="x@example.com", subject=subject, body_excerpt="",
    )


class TestClassifyEmails:
    @patch("gmail.classify.Anthropic")
    def test_parses_response(self, mock_anthropic_cls: MagicMock) -> None:
        client = MagicMock()
        mock_anthropic_cls.return_value = client
        response_text = json.dumps({
            "classifications": [
                {"msg_id": "<1>", "category": "IMPORTANT", "summary": "recruiter"},
                {"msg_id": "<2>", "category": "TRASH_CANDIDATE", "summary": "marketing"},
                {"msg_id": "<3>", "category": "NEUTRAL", "summary": "slack ping"},
            ]
        })
        client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=response_text)],
        )
        emails = [_msg("<1>", "Role"), _msg("<2>", "Sale"), _msg("<3>", "Slack")]
        result = classify_emails(emails, api_key="sk-test")
        assert result["<1>"].category == Category.IMPORTANT
        assert result["<2>"].category == Category.TRASH_CANDIDATE
        assert result["<3>"].category == Category.NEUTRAL

    @patch("gmail.classify.Anthropic")
    def test_empty_list_skips_api(self, mock_anthropic_cls: MagicMock) -> None:
        result = classify_emails([], api_key="sk-test")
        assert result == {}
        mock_anthropic_cls.return_value.messages.create.assert_not_called()

    @patch("gmail.classify.Anthropic")
    def test_fallback_on_parse_error(self, mock_anthropic_cls: MagicMock) -> None:
        client = MagicMock()
        mock_anthropic_cls.return_value = client
        client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="not json")],
        )
        result = classify_emails([_msg("<1>", "x")], api_key="sk-test")
        assert result["<1>"].category == Category.NEUTRAL
        assert "AI unavailable" in result["<1>"].summary
