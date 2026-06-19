"""Auth-related email delivery (URL construction + template dispatch).

Extracted from AuthService so authentication orchestration does not mix
with email rendering / sending concerns. AuthService delegates all
transactional email sends to this class.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.identity.infrastructure import email_templates
from contexts.identity.infrastructure.email import EmailMessage, EmailSender, recipient_digest
from shared_kernel import audit


class AuthEmailService:
    """Handles URL construction and template dispatch for auth emails."""

    def __init__(
        self,
        *,
        db: AsyncSession,
        email_sender: EmailSender,
        public_origin: str,
    ) -> None:
        self._db = db
        self._emailer = email_sender
        self._public_origin = public_origin.rstrip("/")

    async def _deliver(
        self,
        email: str,
        rendered: email_templates.RenderedEmail,
        *,
        user_id: uuid.UUID,
        template: str,
    ) -> None:
        await self._emailer.send(
            EmailMessage(
                to=email,
                subject=rendered.subject,
                text_body=rendered.text_body,
                html_body=rendered.html_body,
                correlation_id=user_id,
                template=template,
            )
        )
        # Audit the send with the template name + a recipient *digest* only --
        # never the plaintext address (SEC: no PII in the audit log).
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="email.sent",
                actor_user_id=user_id,
                resource_type="user",
                resource_id=user_id,
                metadata={"template": template, "recipient": recipient_digest(email)},
            ),
        )

    async def send_email_verification(self, email: str, token: str, *, user_id: uuid.UUID) -> None:
        # The token rides in the URL *fragment* (`#token=`), not the query
        # string: fragments are never sent to the server, so the high-entropy
        # single-use token stays out of access logs, `Referer` headers, and
        # the browser-history query string. The SPA route reads `location.hash`
        # and POSTs the token to `/api/auth/verify-email` (SEC-8).
        link = f"{self._public_origin}/verify-email#token={token}"
        await self._deliver(
            email, email_templates.verify_email(link), user_id=user_id, template="verify_email"
        )

    async def send_email_change_reverify(self, email: str, token: str, *, user_id: uuid.UUID) -> None:
        # Same fragment-token discipline as verification; distinct copy so the
        # new-address owner understands why they're receiving it (R6.06).
        link = f"{self._public_origin}/verify-email#token={token}"
        await self._deliver(
            email,
            email_templates.email_change_reverify(link),
            user_id=user_id,
            template="email_change_reverify",
        )

    async def send_already_registered_notice(self, email: str, *, user_id: uuid.UUID) -> None:
        # Sent when someone tries to register an address that already has an
        # account (SEC-M4). It carries no token and grants no capability -- it
        # only informs the address owner so the registration attempt is not
        # silent, while keeping the "already registered" fact off the HTTP path.
        link = f"{self._public_origin}/login"
        await self._deliver(
            email,
            email_templates.already_registered(link),
            user_id=user_id,
            template="already_registered",
        )

    async def send_password_reset(self, email: str, token: str, *, user_id: uuid.UUID) -> None:
        # Token in the URL fragment -- see `send_email_verification` (SEC-8).
        link = f"{self._public_origin}/password-reset/confirm#token={token}"
        await self._deliver(
            email, email_templates.password_reset(link), user_id=user_id, template="password_reset"
        )


__all__ = ["AuthEmailService"]
