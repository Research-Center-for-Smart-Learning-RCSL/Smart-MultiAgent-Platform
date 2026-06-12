"""Outbound email — interface, dev-mode impl, and real SMTP sender (R6.01/K.6).

`LoggingEmailSender` stays the dev/test sender (the Playwright harness scrapes
its log lines for tokens). `SmtpEmailSender` is the production transport over
`aiosmtplib`; the factory (`application.factory`) picks one based on whether an
SMTP host is configured. Both satisfy the `EmailSender` protocol so the auth /
invite services depend only on the interface.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from typing import Protocol

from loguru import logger


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    text_body: str
    html_body: str | None = None
    # Correlation ID so tests can match a log line to a user action.
    correlation_id: uuid.UUID | None = None


class EmailSender(Protocol):
    async def send(self, msg: EmailMessage) -> None: ...


class LoggingEmailSender:
    """Dev sender — writes structured log lines. Safe in tests."""

    async def send(self, msg: EmailMessage) -> None:
        logger.bind(
            event="email_send",
            to=msg.to,
            subject=msg.subject,
            correlation_id=str(msg.correlation_id) if msg.correlation_id else None,
        ).info(msg.text_body)


def build_mime(msg: EmailMessage, *, from_addr: str) -> MimeMessage:
    """Render an :class:`EmailMessage` to a multipart/alternative MIME message.

    Kept module-level (not a method) so unit tests can assert the wire format
    without standing up a sender.
    """
    mime = MimeMessage()
    mime["From"] = from_addr
    mime["To"] = msg.to
    mime["Subject"] = msg.subject
    if msg.correlation_id is not None:
        # Lets an operator grep the relay/inbox back to the originating action.
        mime["X-SMAP-Correlation-Id"] = str(msg.correlation_id)
    mime.set_content(msg.text_body)
    if msg.html_body:
        mime.add_alternative(msg.html_body, subtype="html")
    return mime


class SmtpEmailSender:
    """Production email transport over SMTP (`aiosmtplib`).

    TLS mode maps to aiosmtplib's two-flag model:
      * ``starttls``  → connect plaintext, upgrade with STARTTLS (port 587)
      * ``implicit``  → TLS on connect (port 465)
      * ``none``      → plaintext (in-cluster relay / MailHog only)

    Credentials are passed in by the factory (sourced from Vault), never read
    here. ``aiosmtplib`` is imported lazily inside :meth:`send` so importing this
    module — which the whole auth layer transitively does — never requires the
    package to be installed in environments that only use the logging sender.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        from_addr: str,
        tls_mode: str = "starttls",
        username: str | None = None,
        password: str | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        self._host = host
        self._port = port
        self._from = from_addr
        self._tls_mode = tls_mode
        self._username = username or None
        self._password = password or None
        self._timeout = timeout_s

    async def send(self, msg: EmailMessage) -> None:
        import aiosmtplib  # lazy import: only the SMTP path needs the package

        mime = build_mime(msg, from_addr=self._from)
        start_tls = self._tls_mode == "starttls"
        use_tls = self._tls_mode == "implicit"
        try:
            await aiosmtplib.send(
                mime,
                hostname=self._host,
                port=self._port,
                username=self._username,
                password=self._password,
                start_tls=start_tls,
                use_tls=use_tls,
                timeout=self._timeout,
            )
        except Exception:
            # Surface the failure to the operator but never leak the body/token.
            logger.bind(event="email_send_failed", to=msg.to, subject=msg.subject).exception(
                "SMTP delivery failed"
            )
            raise


__all__ = [
    "EmailMessage",
    "EmailSender",
    "LoggingEmailSender",
    "SmtpEmailSender",
    "build_mime",
]
