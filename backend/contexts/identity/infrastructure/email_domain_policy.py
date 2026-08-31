"""Construct an :class:`EmailDomainPolicyReader` over the real stores (R19a.13).

The policy used to *be* this module: three Redis keys behind a module-global
30-second cache, with no writer and no version. It is now a two-line factory over
the PostgreSQL authority, the disposable v2 mirror and the legacy triple, because
the resolution rules moved to `application/email_domain_policy_reader.py` where
they can be tested without either store.

Callers take a reader rather than importing this module (`AuthService`,
`AdminService`, and the maintenance commands all do). It remains here so that the
one place that knows *which* concrete stores exist is in `infrastructure/`.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.identity.application.email_domain_policy_reader import EmailDomainPolicyReader
from contexts.identity.infrastructure.email_domain_legacy import (
    RedisLegacyEmailDomainPolicyStore,
)
from contexts.identity.infrastructure.email_domain_mirror import RedisEmailDomainPolicyMirror
from contexts.identity.infrastructure.email_domain_repository import EmailDomainPolicyRepository


def create_reader(db: AsyncSession) -> EmailDomainPolicyReader:
    return EmailDomainPolicyReader(
        repository=EmailDomainPolicyRepository(db),
        mirror=RedisEmailDomainPolicyMirror(),
        legacy=RedisLegacyEmailDomainPolicyStore(),
    )


__all__ = ["create_reader"]
