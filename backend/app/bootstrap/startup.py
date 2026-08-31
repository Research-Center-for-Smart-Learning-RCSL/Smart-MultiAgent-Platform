"""Startup initializer functions for the application lifespan.

Each function performs one isolated startup step. The lifespan iterates
the ``INITIALIZERS`` list in order so adding / removing a step is a
one-line change rather than editing a monolithic async generator.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from app.config.settings import Settings

logger = logging.getLogger(__name__)

# Type alias for an async initializer that receives the resolved settings.
Initializer = Callable[[Settings], Coroutine[Any, Any, None]]


async def configure_logging_step(settings: Settings) -> None:
    from shared_kernel.logging.setup import configure_logging

    configure_logging(settings.logging)


async def confirm_re2_step(_settings: Settings) -> None:
    """SEC-L5: confirm the linear-time regex engine is present at boot."""
    from contexts.workflow.sel.evaluator import confirm_re2_available

    confirm_re2_available()


async def warn_email_step(_settings: Settings) -> None:
    """K.6: warn if SMTP is unconfigured (registration mail undeliverable)."""
    from contexts.identity.application.factory import warn_if_email_unconfigured

    warn_if_email_unconfigured()


async def seed_users_step(settings: Settings) -> None:
    from app.bootstrap.seed import seed_test_users

    await seed_test_users(app_env=settings.app.env)


async def register_activity_validators_step(_settings: Settings) -> None:
    """Register first-party in-process activity validators (R30.05/R30.24).

    Registration must complete before any ``in_process`` type is served, so it runs
    as an ordered startup step rather than relying on incidental import order.
    """
    from app.plugins.activity_validators import register_first_party_validators

    register_first_party_validators()


async def import_email_domain_policy_step(_settings: Settings) -> None:
    """R19a.13: import the legacy Redis policy into PostgreSQL, once, at boot.

    **Deliberately fatal**, unlike `prime_rate_limits_step` below. The limiter
    has compile-time defaults to fall back on; the email-domain policy has none,
    so a boot that continued past an unreadable or corrupt legacy policy would
    serve requests with no policy authority at all — which is the failure this
    whole change exists to end.
    """
    from contexts.identity.application.email_domain_bootstrap import (
        import_legacy_policy_if_absent,
    )
    from contexts.identity.infrastructure.email_domain_legacy import (
        RedisLegacyEmailDomainPolicyStore,
    )
    from contexts.identity.infrastructure.email_domain_repository import (
        EmailDomainPolicyRepository,
    )
    from shared_kernel.db.session import async_session

    # One transaction holds the advisory lock, the re-read and the insert, so
    # concurrent first starts elect one winner and a failure leaves no row.
    async with async_session() as db, db.begin():
        await import_legacy_policy_if_absent(
            db,
            repository=EmailDomainPolicyRepository(db),
            legacy=RedisLegacyEmailDomainPolicyStore(),
        )


async def prime_rate_limits_step(_settings: Settings) -> None:
    """Seed rate-limit policy rows + prime the Redis mirror.

    Best-effort: the limiter falls back to compile-time defaults if this
    hasn't run, so a hiccup here must not block boot.
    """
    try:
        from shared_kernel.auth.ratelimit import prime_policies

        await prime_policies()
    except Exception:  # pragma: no cover - non-fatal boot step
        logger.warning("rate-limit policy prime failed", exc_info=True)


# Ordered list of startup steps. The lifespan iterates this in sequence.
INITIALIZERS: list[Initializer] = [
    configure_logging_step,
    confirm_re2_step,
    warn_email_step,
    register_activity_validators_step,
    seed_users_step,
    # After seed_users_step: the imported row's `updated_by_user_id` is NULL, but
    # a later Admin write FKs to `users`, and running the policy import before
    # the schema has any user rows has no ordering benefit either way. Before the
    # rate-limit prime because this one is fatal and that one is not — a boot
    # that is going to fail should fail before doing best-effort work.
    import_email_domain_policy_step,
    prime_rate_limits_step,
]

__all__ = ["INITIALIZERS", "Initializer"]
