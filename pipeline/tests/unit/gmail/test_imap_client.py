"""Tests for IMAP client fetch/search wrappers.

Mocks ``imaplib.IMAP4_SSL`` directly and verifies the wrapper calls the
expected sequence of IMAP commands.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from gmail.imap_client import ImapConfig, fetch_unread_last_24h, parse_message


class TestFetchUnreadLast24h:
    @patch("imaplib.IMAP4_SSL")
    def test_happy_path(self, mock_imap_cls: MagicMock) -> None:
        m = MagicMock()
        mock_imap_cls.return_value = m
        m.login.return_value = ("OK", [b"LOGIN ok"])
        m.select.return_value = ("OK", [b"42"])
        m.uid.side_effect = [
            ("OK", [b"1 2 3"]),
            ("OK", [(b"1 (BODY[] {...})", b"From: a@x\r\nSubject: A\r\nMessage-ID: <1@x>\r\n\r\nbody1")]),
            ("OK", [(b"2 (BODY[] {...})", b"From: b@x\r\nSubject: B\r\nMessage-ID: <2@x>\r\n\r\nbody2")]),
            ("OK", [(b"3 (BODY[] {...})", b"From: c@x\r\nSubject: C\r\nMessage-ID: <3@x>\r\n\r\nbody3")]),
        ]
        cfg = ImapConfig(user="me@gmail.com", password="pw")
        emails = fetch_unread_last_24h(cfg)
        assert len(emails) == 3
        assert emails[0].msg_id == "<1@x>"
        assert emails[0].subject == "A"
        m.login.assert_called_once_with("me@gmail.com", "pw")
        m.select.assert_called_once_with("INBOX")

    @patch("imaplib.IMAP4_SSL")
    def test_empty_inbox(self, mock_imap_cls: MagicMock) -> None:
        m = MagicMock()
        mock_imap_cls.return_value = m
        m.login.return_value = ("OK", [b"ok"])
        m.select.return_value = ("OK", [b"0"])
        m.uid.return_value = ("OK", [b""])
        cfg = ImapConfig(user="me@gmail.com", password="pw")
        assert fetch_unread_last_24h(cfg) == []


class TestParseMessage:
    def test_extracts_core_fields(self) -> None:
        raw = (
            b"From: Foo <foo@example.com>\r\n"
            b"Subject: Test Subject\r\n"
            b"Message-ID: <abc123@example.com>\r\n"
            b"Date: Mon, 12 Apr 2026 10:00:00 +0000\r\n"
            b"\r\n"
            b"Hello world. This is the body."
        )
        msg = parse_message(raw)
        assert msg.msg_id == "<abc123@example.com>"
        assert msg.sender == "Foo <foo@example.com>"
        assert msg.subject == "Test Subject"
        assert msg.received_at.startswith("2026-04-12")
        assert "Hello world" in msg.body_excerpt

    def test_handles_missing_subject(self) -> None:
        raw = b"From: x@y\r\nMessage-ID: <m@y>\r\nDate: Mon, 12 Apr 2026 10:00:00 +0000\r\n\r\nbody"
        assert parse_message(raw).subject == ""

    def test_handles_missing_date(self) -> None:
        raw = b"From: x@y\r\nSubject: s\r\nMessage-ID: <m@y>\r\n\r\nbody"
        assert parse_message(raw).received_at == ""
