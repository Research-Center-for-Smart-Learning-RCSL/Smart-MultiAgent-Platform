"""Construct an `AuthService` with its infrastructure dependencies wired.

Routers call this instead of reaching into `contexts.identity.infrastructure`
directly — the import-linter forbids `app.api → *.infrastructure`, so this
module is the single integration point that knows *how* to assemble the
service. Tests may monkey-patch `email_sender_factory` to swap in a fake.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.identity.application.auth_service import AuthService
from contexts.identity.infrastructure.email import (
    EmailMessage,
    EmailSender,
    LoggingEmailSender,
    SmtpEmailSender,
)
from shared_kernel.auth.password import PasswordHasher

_hasher = PasswordHasher()

# Vault KV path holding the SMTP credentials (username/password). Connection
# parameters (host/port/from/tls) live in settings; only the secrets are here.
_SMTP_KV_PATH: Final = "smap/config/smtp"


def email_configured() -> bool:
    """True when a real SMTP transport is configured (``smtp_host`` is set)."""
    return bool(get_settings().email.smtp_host)


def _default_email_sender() -> EmailSender:
    """Pick SMTP when configured, else the dev/log sender (fail-open).

    Self-hosted operators may legitimately run mail-less in a closed lab, so an
    unconfigured transport degrades to logging rather than refusing to start.
    """
    cfg = get_settings().email
    if not cfg.smtp_host:
        return LoggingEmailSender()
    username: str | None = None
    password: str | None = None
    try:
        from shared_kernel.auth.clients import get_vault_client

        creds = get_vault_client().kv_get(_SMTP_KV_PATH)
        username = str(creds.get("username", "")) or None
        password = str(creds.get("password", "")) or None
    except Exception:
        # No creds in Vault is valid for an unauthenticated in-cluster relay /
        # MailHog; log and proceed without auth rather than dropping to logging.
        logger.bind(event="smtp_creds_missing", path=_SMTP_KV_PATH).warning(
            "SMTP host is configured but Vault credentials are unreadable; "
            "sending without authentication"
        )
    return SmtpEmailSender(
        host=cfg.smtp_host,
        port=cfg.smtp_port,
        from_addr=cfg.smtp_from,
        tls_mode=cfg.smtp_tls_mode,
        username=username,
        password=password,
        timeout_s=cfg.smtp_timeout_s,
    )


# Swap-in for tests; production selects SMTP vs logging at call time.
email_sender_factory: Callable[[], EmailSender] = _default_email_sender


class LazyEmailSender:
    """Defers real-sender construction until the first ``send``.

    ``AuthService`` / ``InviteService`` are built per-request, but most requests
    (login, refresh, /me, list-invites, accept) never send mail. Building the
    SMTP sender eagerly would read the Vault SMTP secret on every one of those
    hot-path requests; this wrapper makes construction free and only pays the
    Vault read when a message is actually dispatched (register / reset /
    change-email / invite-create). The built sender is cached for the request.
    """

    def __init__(self, factory: Callable[[], EmailSender]) -> None:
        self._factory = factory
        self._real: EmailSender | None = None

    async def send(self, msg: EmailMessage) -> None:
        if self._real is None:
            self._real = self._factory()
        await self._real.send(msg)


def warn_if_email_unconfigured() -> None:
    """Startup check: in prod with no SMTP host, registration mail is undeliverable.

    Logs a single loud warning (called once from the app lifespan) instead of
    failing the boot — the logging sender stays active for mail-less labs.
    """
    s = get_settings()
    if s.app.env == "prod" and not s.email.smtp_host:
        logger.bind(event="smtp_unconfigured").warning(
            "SMAP_APP_ENV=prod but no SMTP host is configured (SMTP_HOST unset): "
            "verification, password-reset, and invite emails will NOT be delivered. "
            "Registration through the UI is effectively disabled until SMTP is set up."
        )

    # Port/TLS-mode mismatch: a plaintext STARTTLS connect to an implicit-TLS
    # port (465) — or an implicit-TLS connect to the STARTTLS port (587) — does
    # not error cleanly; it usually hangs until the timeout, so every outbound
    # mail stalls. Warn loudly at startup rather than discovering it per-send.
    if s.email.smtp_host:
        port, mode = s.email.smtp_port, s.email.smtp_tls_mode
        if port == 465 and mode == "starttls":
            logger.bind(event="smtp_tls_mismatch", port=port, tls_mode=mode).warning(
                "SMTP_PORT=465 (implicit TLS) with SMTP_TLS_MODE=starttls: a plaintext "
                "STARTTLS connect to an implicit-TLS port will hang. Use SMTP_TLS_MODE=implicit."
            )
        elif port == 587 and mode == "implicit":
            logger.bind(event="smtp_tls_mismatch", port=port, tls_mode=mode).warning(
                "SMTP_PORT=587 (STARTTLS) with SMTP_TLS_MODE=implicit: a TLS-on-connect "
                "handshake to a plaintext port will hang. Use SMTP_TLS_MODE=starttls."
            )


def create_auth_service(db: AsyncSession, *, public_origin: str) -> AuthService:
    return AuthService(
        db=db,
        hasher=_hasher,
        email_sender=LazyEmailSender(email_sender_factory),
        public_origin=public_origin,
    )


__all__ = [
    "LazyEmailSender",
    "create_auth_service",
    "email_configured",
    "email_sender_factory",
    "warn_if_email_unconfigured",
]
