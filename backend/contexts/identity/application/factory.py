"""Construct an `AuthService` with its infrastructure dependencies wired.

Routers call this instead of reaching into `contexts.identity.infrastructure`
directly — the import-linter forbids `app.api → *.infrastructure`, so this
module is the single integration point that knows *how* to assemble the
service. Tests may monkey-patch `email_sender_factory` to swap in a fake.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.identity.application.auth_service import AuthService
from contexts.identity.infrastructure.email import EmailSender, LoggingEmailSender
from shared_kernel.auth.password import PasswordHasher

_hasher = PasswordHasher()

# Swap-in for tests; production uses the default LoggingEmailSender for now
# (Phase I replaces with real SMTP).
email_sender_factory: Callable[[], EmailSender] = LoggingEmailSender


def create_auth_service(db: AsyncSession, *, public_origin: str) -> AuthService:
    return AuthService(
        db=db,
        hasher=_hasher,
        email_sender=email_sender_factory(),
        public_origin=public_origin,
    )


__all__ = ["create_auth_service", "email_sender_factory"]
