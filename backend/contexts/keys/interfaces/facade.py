"""Keys facade — cross-context read/revoke surface.

Other contexts (tenancy's member-removal flow; E's agent router) use this
facade instead of reaching into `contexts.keys.infrastructure` directly.
Keeps the import graph acyclic and makes the contract between contexts
explicit.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.application.carry_service import CarryService
from contexts.keys.domain.groups import KeyGroup
from contexts.keys.domain.models import ApiKey
from contexts.keys.infrastructure.group_repository import KeyGroupRepository


class KeysFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_keys_carried_in_project(
        self, project_id: uuid.UUID
    ) -> list[ApiKey]:
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
        from contexts.keys.domain.errors import KeyNotFound  # noqa: PLC0415
        from contexts.keys.infrastructure.repositories import (  # noqa: PLC0415
            ApiKeyRepository,
        )
        from shared_kernel.security import envelope as env  # noqa: PLC0415

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


__all__ = ["KeysFacade"]
