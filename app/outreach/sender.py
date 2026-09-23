"""Email delivery.

Two providers ship: `console` (writes a .eml to artifacts/outbox - the default,
so a test run can never actually email a clinic) and `smtp` (any authenticated
business mailbox, including Gmail with an app password).

A send is never retried automatically when the outcome is uncertain: an
ambiguous failure is recorded as SEND_FAILED for a human to look at.
"""

from __future__ import annotations

import mimetypes
import smtplib
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Protocol

from app.config import Settings, get_settings


#: Failures that leave delivery genuinely unknown: the server may already have
#: accepted the message when the connection dropped.
AMBIGUOUS_ERRORS = (
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPResponseException,
    TimeoutError,
    ConnectionResetError,
)


@dataclass
class SendResult:
    status: str  # SENT | SEND_FAILED | SEND_UNCERTAIN
    message_id: str | None = None
    provider: str = "console"
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "SENT"

    @property
    def uncertain(self) -> bool:
        return self.status == "SEND_UNCERTAIN"


class EmailProvider(Protocol):
    name: str
    #: True when the provider actually delivers to a third party, so demo and
    #: video links must resolve somewhere the recipient can reach.
    requires_public_links: bool

    def send(
        self,
        recipient: str,
        subject: str,
        text: str,
        html: str,
        attachments: list[Path] | None = None,
    ) -> SendResult: ...


def build_message(
    settings: Settings,
    recipient: str,
    subject: str,
    text: str,
    html: str,
    attachments: list[Path] | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((settings.sender_name, settings.sender_email))
    message["To"] = recipient
    message["Subject"] = subject
    message["Message-ID"] = make_msgid(domain=settings.sender_email.split("@")[-1])
    message["Reply-To"] = settings.sender_email
    if settings.unsubscribe_mailto:
        message["List-Unsubscribe"] = f"<mailto:{settings.unsubscribe_mailto}>"
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    for path in attachments or []:
        if not path.exists():
            continue
        guessed, _ = mimetypes.guess_type(path.name)
        maintype, _, subtype = (guessed or "application/octet-stream").partition("/")
        message.add_attachment(
            path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name
        )
    return message


class ConsoleEmailProvider:
    """Writes the exact message to disk instead of sending it."""

    name = "console"
    requires_public_links = False

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def send(
        self,
        recipient: str,
        subject: str,
        text: str,
        html: str,
        attachments: list[Path] | None = None,
    ) -> SendResult:
        self.settings.ensure_dirs()
        message = build_message(self.settings, recipient, subject, text, html, attachments)
        filename = f"{uuid.uuid4().hex[:8]}-{recipient.replace('@', '_at_')}.eml"
        path = self.settings.outbox_dir / filename
        path.write_bytes(bytes(message))
        return SendResult(status="SENT", message_id=message["Message-ID"], provider=self.name)


class SmtpEmailProvider:
    name = "smtp"
    requires_public_links = True

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def send(
        self,
        recipient: str,
        subject: str,
        text: str,
        html: str,
        attachments: list[Path] | None = None,
    ) -> SendResult:
        settings = self.settings
        if not settings.smtp_host:
            return SendResult(status="SEND_FAILED", provider=self.name, error="SMTP_HOST is not set")
        message = build_message(settings, recipient, subject, text, html, attachments)
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                if settings.smtp_user and settings.smtp_password:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(message)
        except smtplib.SMTPRecipientsRefused as exc:
            # Definitive rejection: nothing was delivered.
            return SendResult(
                status="SEND_FAILED", provider=self.name, error=f"recipient refused: {exc}"
            )
        except AMBIGUOUS_ERRORS as exc:
            # The server may have accepted the message before the failure, so
            # this is recorded as uncertain and never resent automatically.
            return SendResult(
                status="SEND_UNCERTAIN", provider=self.name, error=f"{type(exc).__name__}: {exc}"
            )
        except Exception as exc:  # noqa: BLE001 - recorded, never auto-retried
            return SendResult(
                status="SEND_FAILED", provider=self.name, error=f"{type(exc).__name__}: {exc}"
            )
        return SendResult(status="SENT", message_id=message["Message-ID"], provider=self.name)


PROVIDERS = {"console": ConsoleEmailProvider, "smtp": SmtpEmailProvider}


def get_email_provider(name: str | None = None, settings: Settings | None = None) -> EmailProvider:
    settings = settings or get_settings()
    key = (name or settings.email_provider or "console").lower()
    try:
        return PROVIDERS[key](settings)  # type: ignore[return-value]
    except KeyError:
        raise ValueError(f"unknown email provider {key!r}") from None
