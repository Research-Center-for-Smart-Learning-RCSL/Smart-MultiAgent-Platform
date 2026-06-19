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
    seed_users_step,
    prime_rate_limits_step,
]

__all__ = ["INITIALIZERS", "Initializer"]
