"""Tests for the SMTP config + send path in etl/automation/notify.py (fully mocked)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from etl.automation.notify import EmailConfig, send

# ── EmailConfig.from_env ─────────────────────────────────────────────────────


class TestFromEnv:
    def test_returns_none_when_user_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PORTAL_SMTP_USER", raising=False)
        monkeypatch.setenv("PORTAL_SMTP_PASSWORD", "pw")
        assert EmailConfig.from_env() is None

    def test_returns_none_when_password_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PORTAL_SMTP_USER", "me@gmail.com")
        monkeypatch.delenv("PORTAL_SMTP_PASSWORD", raising=False)
        assert EmailConfig.from_env() is None

    def test_returns_none_when_both_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PORTAL_SMTP_USER", raising=False)
        monkeypatch.delenv("PORTAL_SMTP_PASSWORD", raising=False)
        assert EmailConfig.from_env() is None

    def test_defaults_to_gmail_and_self_email(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PORTAL_SMTP_USER", "me@gmail.com")
        monkeypatch.setenv("PORTAL_SMTP_PASSWORD", "apppw")
        monkeypatch.delenv("PORTAL_SMTP_HOST", raising=False)
        monkeypatch.delenv("PORTAL_SMTP_PORT", raising=False)
        monkeypatch.delenv("PORTAL_EMAIL_FROM", raising=False)
        monkeypatch.delenv("PORTAL_EMAIL_TO", raising=False)
        cfg = EmailConfig.from_env()
        assert cfg is not None
        assert cfg.smtp_host == "smtp.gmail.com"
        assert cfg.smtp_port == 587
        assert cfg.smtp_user == "me@gmail.com"
        assert cfg.email_from == "me@gmail.com"
        assert cfg.email_to == "me@gmail.com"

    def test_respects_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PORTAL_SMTP_USER", "sender@x.com")
        monkeypatch.setenv("PORTAL_SMTP_PASSWORD", "pw")
        monkeypatch.setenv("PORTAL_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("PORTAL_SMTP_PORT", "2525")
        monkeypatch.setenv("PORTAL_EMAIL_FROM", "from@x.com")
        monkeypatch.setenv("PORTAL_EMAIL_TO", "to@x.com")
        cfg = EmailConfig.from_env()
        assert cfg is not None
        assert cfg.smtp_host == "smtp.example.com"
        assert cfg.smtp_port == 2525
        assert cfg.email_from == "from@x.com"
        assert cfg.email_to == "to@x.com"


# ── send() ───────────────────────────────────────────────────────────────────


def _cfg() -> EmailConfig:
    return EmailConfig(
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_user="me@gmail.com",
        smtp_password="pw",
        email_from="me@gmail.com",
        email_to="me@gmail.com",
    )


class TestSend:
    def test_uses_starttls_and_correct_host_port(self) -> None:
        with patch("etl.automation.notify.smtplib.SMTP") as mock_smtp:
            instance = mock_smtp.return_value.__enter__.return_value
            send("subj", "<p>html</p>", "plain", _cfg())
            mock_smtp.assert_called_once()
            args, kwargs = mock_smtp.call_args
            assert args[0] == "smtp.gmail.com"
            assert args[1] == 587
            instance.starttls.assert_called_once()
            instance.login.assert_called_once_with("me@gmail.com", "pw")
            instance.send_message.assert_called_once()

    def test_message_has_text_and_html_parts(self) -> None:
        with patch("etl.automation.notify.smtplib.SMTP") as mock_smtp:
            instance = mock_smtp.return_value.__enter__.return_value
            send("subj", "<p>html body</p>", "plain body", _cfg())
            msg = instance.send_message.call_args.args[0]
            assert msg["Subject"] == "subj"
            assert msg["From"] == "me@gmail.com"
            assert msg["To"] == "me@gmail.com"
            assert msg.is_multipart()
            parts = list(msg.walk())
            subtypes = {p.get_content_subtype() for p in parts if p.get_content_maintype() == "text"}
            # MIMEMultipart('alternative') has text/plain + text/html children
            assert "plain" in subtypes
            assert "html" in subtypes

    def test_raises_on_smtp_error(self) -> None:
        import smtplib as _smtplib

        with patch("etl.automation.notify.smtplib.SMTP") as mock_smtp:
            instance = mock_smtp.return_value.__enter__.return_value
            instance.login.side_effect = _smtplib.SMTPAuthenticationError(535, b"bad credentials")
            with pytest.raises(_smtplib.SMTPAuthenticationError):
                send("subj", "<p>html</p>", "plain", _cfg())

    def test_raises_when_smtp_construction_fails(self) -> None:
        with (
            patch("etl.automation.notify.smtplib.SMTP", side_effect=ConnectionRefusedError("no route")),
            pytest.raises(ConnectionRefusedError),
        ):
            send("subj", "<p>html</p>", "plain", _cfg())
