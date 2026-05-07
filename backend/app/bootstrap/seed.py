"""Test-environment user seeding.

Runs at startup when `SMAP_APP_ENV=test` and `SMAP_SEED_ON_STARTUP=true`.
Idempotent: existing emails are left alone. Used by `compose.test.yml` so
Playwright E2E specs can log in without touching the email-verification
flow.

Never enabled in prod: gated on `app.env == "test"`. Even if an operator
sets `SMAP_SEED_ON_STARTUP=true` in prod, the env check short-circuits.
"""

from __future__ import annotations

import logging
import os

from contexts.identity.domain.models import UserStatus
from contexts.identity.infrastructure.repositories import UserRepository
from shared_kernel.auth.password import PasswordHasher
from shared_kernel.db.session import async_session

logger = logging.getLogger(__name__)


async def seed_test_users(*, app_env: str) -> None:
    if app_env != "test":
        return
    if os.environ.get("SMAP_SEED_ON_STARTUP", "").lower() not in {"1", "true", "yes"}:
        return

    seeds = [
        (
            os.environ.get("SMAP_SEED_USER_EMAIL", "e2e-user@smap.test"),
            os.environ.get("SMAP_SEED_USER_PASSWORD", "E2eP@ssw0rd!Str0ng"),
        ),
        (
            os.environ.get("SMAP_SEED_ADMIN_EMAIL", "e2e-admin@smap.test"),
            os.environ.get("SMAP_SEED_ADMIN_PASSWORD", "E2eAdm1n!Str0ng"),
        ),
    ]

    hasher = PasswordHasher()
    async with async_session() as db:
        async with db.begin():
            users = UserRepository(db)
            for email, password in seeds:
                if await users.get_active_by_email(email) is not None:
                    logger.info("seed: user already present, skipping email=%s", email)
                    continue
                user = await users.insert(
                    email=email,
                    password_hash=hasher.hash(password),
                    status=UserStatus.PENDING,
                )
                if not await users.mark_verified(user.id):
                    logger.warning("seed: mark_verified no-op for %s", email)
                else:
                    logger.info("seed: created verified user %s", email)


__all__ = ["seed_test_users"]
