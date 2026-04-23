"""Outbound email — interface + dev-mode impl.

SMTP credentials and the real SMTP client land in Phase I (notifications).
Phase C only needs to dispatch the verification + password-reset + invite
links; in dev we log to stdout so the Playwright harness can scrape tokens.

The protocol is written so replacing `LoggingEmailSender` with an
`aiosmtplib`-based implementation is a single DI swap.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
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


__all__ = ["EmailMessage", "EmailSender", "LoggingEmailSender"]
