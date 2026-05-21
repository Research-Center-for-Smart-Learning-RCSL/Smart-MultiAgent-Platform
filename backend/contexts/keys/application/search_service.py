"""Search-key use-cases (D.11 / §22.9a)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.domain.errors import KeyNotFound, SearchActivationConflict
from contexts.keys.domain.models import mask_preview
from contexts.keys.domain.search import SearchKey, SearchProvider
from contexts.keys.infrastructure.probes.base import ProbeStatus
from contexts.keys.infrastructure.search_probes import probe_search
from contexts.keys.infrastructure.search_repository import SearchKeyRepository
from shared_kernel import audit
from shared_kernel.auth.clients import get_redis
from shared_kernel.security import envelope as env


class SearchKeyService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = SearchKeyRepository(db)

    async def list_for_project(self, project_id: uuid.UUID) -> list[SearchKey]:
        return await self._repo.list_for_project(project_id)

    async def upload(
        self,
        *,
        project_id: uuid.UUID,
        provider: SearchProvider,
        secret: str,
        config: dict[str, Any],
        actor_user_id: uuid.UUID,
        request_id: uuid.UUID | None = None,
    ) -> SearchKey:
        probe = await probe_search(provider, secret, config)

        key_id = uuid.uuid4()
        envelope = env.encrypt_envelope(secret.encode("utf-8"), env.search_key_aad(key_id))
        preview = mask_preview(secret)
        secret = ""

        now = datetime.now(tz=UTC)
        sk = await self._repo.insert(
            key_id=key_id,
            project_id=project_id,
            provider=provider,
            envelope=envelope,
            masked_preview=preview,
            test_status=probe.status,
            test_error=probe.error,
            last_test_at=now,
            config=config,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="search_key.uploaded",
                actor_user_id=actor_user_id,
                resource_type="search_key",
                resource_id=sk.id,
                metadata={
                    "project_id": str(project_id),
                    "provider": provider.value,
                },
                request_id=request_id,
            ),
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=(
                    "search_key.test_success" if probe.status is ProbeStatus.OK else "search_key.test_failed"
                ),
                actor_user_id=actor_user_id,
                resource_type="search_key",
                resource_id=sk.id,
                # Coarse category only — never the raw probe error (SEC-6).
                metadata={
                    "test_status": probe.status.value,
                    "error_category": probe.audit_category(),
                },
                request_id=request_id,
            ),
        )
        return sk

    async def retest(
        self,
        *,
        project_id: uuid.UUID,
        key_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        request_id: uuid.UUID | None = None,
    ) -> SearchKey:
        loaded = await self._repo.get_active_with_envelope(key_id)
        if loaded is None:
            raise KeyNotFound(str(key_id))
        sk, record = loaded
        # Path project scope must match the row's — otherwise any member of
        # project B could retest a key belonging to project A.
        if sk.project_id != project_id:
            raise KeyNotFound(str(key_id))
        plaintext = env.decrypt_envelope(record, env.search_key_aad(key_id))
        try:
            result = await probe_search(sk.provider, plaintext.decode("utf-8"), sk.config)
        finally:
            plaintext = b"\x00" * len(plaintext)
        await self._repo.update_probe(
            key_id=key_id,
            test_status=result.status,
            test_error=result.error,
            last_test_at=datetime.now(tz=UTC),
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=(
                    "search_key.test_success" if result.status is ProbeStatus.OK else "search_key.test_failed"
                ),
                actor_user_id=actor_user_id,
                resource_type="search_key",
                resource_id=key_id,
                # Coarse category only — never the raw probe error (SEC-6).
                metadata={
                    "test_status": result.status.value,
                    "error_category": result.audit_category(),
                },
                request_id=request_id,
            ),
        )
        updated = await self._repo.get_active(key_id)
        assert updated is not None
        return updated

    async def activate(
        self,
        *,
        project_id: uuid.UUID,
        key_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        request_id: uuid.UUID | None = None,
    ) -> None:
        sk = await self._repo.get_active(key_id)
        if sk is None or sk.project_id != project_id:
            raise KeyNotFound(str(key_id))
        try:
            await self._repo.atomic_activate(key_id=key_id, project_id=project_id)
        except IntegrityError as exc:
            # Two concurrent activators raced; the partial-unique-active index
            # rejects the second writer. Translate for the 409 surface.
            raise SearchActivationConflict(str(key_id)) from exc
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="search_key.activated",
                actor_user_id=actor_user_id,
                resource_type="search_key",
                resource_id=key_id,
                metadata={"project_id": str(project_id)},
                request_id=request_id,
            ),
        )
        # §12.4: activation flip invalidates the Redis `search:{hash}` cache
        # for this project. The cache-key layout lives with Phase E; we
        # publish a plain message so the cache owner can choose the strategy.
        await get_redis().publish("search_key.activated", f"{project_id}:{key_id}")

    async def delete(
        self,
        *,
        project_id: uuid.UUID,
        key_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        request_id: uuid.UUID | None = None,
    ) -> None:
        sk = await self._repo.get_active(key_id)
        if sk is None or sk.project_id != project_id:
            raise KeyNotFound(str(key_id))
        await self._repo.soft_delete(key_id, at=datetime.now(tz=UTC))
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="search_key.deleted",
                actor_user_id=actor_user_id,
                resource_type="search_key",
                resource_id=key_id,
                metadata={"was_active": sk.is_active},
                request_id=request_id,
            ),
        )


__all__ = ["SearchKeyService"]
