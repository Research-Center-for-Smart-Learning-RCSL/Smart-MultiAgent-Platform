"""Keys facade — cross-context read/revoke surface.

Other contexts (tenancy's member-removal flow; E's agent router) use this
facade instead of reaching into `contexts.keys.infrastructure` directly.
Keeps the import graph acyclic and makes the contract between contexts
explicit.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.application.carry_service import CarryService
from contexts.keys.domain.errors import CapabilityMismatch as KeysCapabilityMismatch
from contexts.keys.domain.groups import KeyGroup
from contexts.keys.domain.models import ApiKey
from contexts.keys.domain.providers import (
    ApiKeyProvider,
    CapabilityRequirement,
    ProviderCapability,
    assert_capability,
    supports,
)
from contexts.keys.infrastructure import tables as _t
from contexts.keys.infrastructure.group_repository import KeyGroupRepository
from contexts.keys.infrastructure.repositories import ApiKeyRepository


class KeysFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._keys = ApiKeyRepository(db)

    async def list_keys_carried_in_project(self, project_id: uuid.UUID) -> list[ApiKey]:
        return await CarryService(self._db).list_in_project(project_id)

    async def get_key_group(self, group_id: uuid.UUID) -> KeyGroup | None:
        """Return the active Key Group (or None if missing / soft-deleted).

        Used by the agents context (E.1) to validate that an attached
        `key_group_id` belongs to the agent's project and to the
        expected capability class.
        """
        return await KeyGroupRepository(self._db).get_active(group_id)

    async def unwrap_api_key_plaintext(self, key_id: uuid.UUID) -> bytes:
        """Decrypt an `api_keys` row's envelope and return the plaintext.

        Used by the knowledge context (E.5 / E.6) so embedder + reranker
        adapters can sign outbound provider calls without re-implementing
        the envelope unwrap. Caller is responsible for *zeroising* the
        returned bytes after use.
        """
        from contexts.keys.domain.errors import KeyNotFound
        from contexts.keys.infrastructure.repositories import (
            ApiKeyRepository,
        )
        from shared_kernel.security import envelope as env

        repo = ApiKeyRepository(self._db)
        loaded = await repo.get_active_with_envelope(key_id)
        if loaded is None:
            raise KeyNotFound(str(key_id))
        _key, record = loaded
        return env.decrypt_envelope(record, env.api_key_aad(key_id))

    async def revoke_carries_for_user_leaving_project(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        request_id: uuid.UUID | None = None,
    ) -> list[uuid.UUID]:
        """Fanout entry point for R7.04.

        Tenancy's project-member-removal code calls this with the caller's
        open `AsyncSession`, so the cascade shares the membership change's
        unit of work.
        """
        return await CarryService(self._db).revoke_for_leaving_user(
            user_id=user_id,
            project_id=project_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )


    async def get_key(self, key_id: uuid.UUID) -> ApiKey | None:
        """Return the active (non-deleted) API key, or ``None``."""
        return await self._keys.get_active(key_id)

    async def validate_key_capability(
        self,
        key_id: uuid.UUID,
        capability: ProviderCapability,
    ) -> bool:
        """Check whether the key's provider supports *capability*.

        Returns ``False`` if the key is missing/deleted or if the provider
        does not support the given capability.
        """
        key = await self._keys.get_active(key_id)
        if key is None:
            return False
        return supports(ApiKeyProvider(key.provider.value), capability)

    async def is_key_in_project_scope(
        self,
        key_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> bool:
        """True if *key_id* is a member of any active key-group in *project_id*.

        Encapsulates the ``key_group_members JOIN key_groups`` scope check that
        the knowledge context uses when validating embed/rerank keys.
        """
        row = (
            await self._db.execute(
                sa.select(sa.literal(1))
                .select_from(
                    _t.key_group_members.join(
                        _t.key_groups,
                        _t.key_groups.c.id == _t.key_group_members.c.group_id,
                    )
                )
                .where(
                    sa.and_(
                        _t.key_group_members.c.key_id == key_id,
                        _t.key_groups.c.project_id == project_id,
                        _t.key_groups.c.deleted_at.is_(None),
                    )
                )
                .limit(1)
            )
        ).first()
        return row is not None

    async def list_keys_for_group(self, group_id: uuid.UUID) -> list[ApiKey]:
        """Return every active API key that is a member of *group_id*."""
        # Fetch member key_ids, then batch-resolve via the repository so the
        # row-to-model mapping stays encapsulated inside ApiKeyRepository.
        id_rows = (
            await self._db.execute(
                sa.select(_t.key_group_members.c.key_id).where(
                    _t.key_group_members.c.group_id == group_id,
                )
            )
        ).all()
        keys: list[ApiKey] = []
        for row in id_rows:
            key = await self._keys.get_active(row.key_id)
            if key is not None:
                keys.append(key)
        return keys


# Re-export domain types so cross-context consumers need only one import path.
__all__ = [
    "ApiKeyProvider",
    "CapabilityRequirement",
    "KeysCapabilityMismatch",
    "KeysFacade",
    "ProviderCapability",
    "assert_capability",
]
