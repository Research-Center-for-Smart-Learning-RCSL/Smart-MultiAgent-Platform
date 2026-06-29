"""Repository for `search_keys` (D.11)."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.domain.search import SearchKey, SearchProvider
from contexts.keys.infrastructure import tables as t
from contexts.keys.infrastructure.probes.base import ProbeStatus
from shared_kernel.infra.vault import EnvelopeRecord


def _row_to_sk(row: Any) -> SearchKey:
    return SearchKey(
        id=row.id,
        project_id=row.project_id,
        provider=SearchProvider(row.provider),
        masked_preview=row.masked_preview,
        test_status=ProbeStatus(row.test_status),
        test_error=row.test_error,
        last_test_at=row.last_test_at,
        is_active=row.is_active,
        config=dict(row.config or {}),
        transit_key_version=row.transit_key_version,
        hmac_key_version=row.hmac_key_version,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


def _row_to_env(row: Any) -> EnvelopeRecord:
    return EnvelopeRecord(
        ciphertext=bytes(row.ciphertext),
        nonce=bytes(row.nonce),
        dek_wrapped=row.dek_wrapped,
        ciphertext_hmac=bytes(row.ciphertext_hmac),
        transit_key_version=row.transit_key_version,
        hmac_key_version=row.hmac_key_version,
    )


class SearchKeyRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def insert(
        self,
        *,
        key_id: uuid.UUID,
        project_id: uuid.UUID,
        provider: SearchProvider,
        envelope: EnvelopeRecord,
        masked_preview: str,
        test_status: ProbeStatus,
        test_error: str | None,
        last_test_at: Any,
        config: dict[str, Any],
    ) -> SearchKey:
        stmt = (
            t.search_keys.insert()
            .values(
                id=key_id,
                project_id=project_id,
                provider=provider.value,
                ciphertext=envelope.ciphertext,
                nonce=envelope.nonce,
                dek_wrapped=envelope.dek_wrapped,
                ciphertext_hmac=envelope.ciphertext_hmac,
                transit_key_version=envelope.transit_key_version,
                hmac_key_version=envelope.hmac_key_version,
                masked_preview=masked_preview,
                test_status=test_status.value,
                test_error=test_error,
                last_test_at=last_test_at,
                is_active=False,
                config=config,
            )
            .returning(t.search_keys)
        )
        row = (await self._db.execute(stmt)).one()
        return _row_to_sk(row)

    async def list_for_project(self, project_id: uuid.UUID) -> list[SearchKey]:
        rows = (
            await self._db.execute(
                t.search_keys.select()
                .where(
                    sa.and_(
                        t.search_keys.c.project_id == project_id,
                        t.search_keys.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.search_keys.c.created_at.desc())
            )
        ).all()
        return [_row_to_sk(r) for r in rows]

    async def get_active(self, key_id: uuid.UUID) -> SearchKey | None:
        row = (
            await self._db.execute(
                t.search_keys.select().where(
                    sa.and_(
                        t.search_keys.c.id == key_id,
                        t.search_keys.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        return _row_to_sk(row) if row else None

    async def get_active_with_envelope(
        self, key_id: uuid.UUID, *, project_id: uuid.UUID
    ) -> tuple[SearchKey, EnvelopeRecord] | None:
        # Project scope is a WHERE predicate, not just a caller-side check, so the
        # decryptable envelope can never be loaded for another tenant's key even
        # if a future caller forgets to compare project_id (the AAD binds only the
        # key_id, so it would not catch a cross-tenant decrypt on its own).
        row = (
            await self._db.execute(
                t.search_keys.select().where(
                    sa.and_(
                        t.search_keys.c.id == key_id,
                        t.search_keys.c.project_id == project_id,
                        t.search_keys.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        if row is None:
            return None
        return _row_to_sk(row), _row_to_env(row)

    async def update_probe(
        self,
        *,
        key_id: uuid.UUID,
        test_status: ProbeStatus,
        test_error: str | None,
        last_test_at: Any,
    ) -> None:
        await self._db.execute(
            t.search_keys.update()
            .where(
                sa.and_(
                    t.search_keys.c.id == key_id,
                    t.search_keys.c.deleted_at.is_(None),
                )
            )
            .values(test_status=test_status.value, test_error=test_error, last_test_at=last_test_at)
        )

    async def atomic_activate(self, *, key_id: uuid.UUID, project_id: uuid.UUID) -> None:
        """Flip `key_id` to active and deactivate every sibling in one TX.

        The partial-unique index `uq_search_keys_active_per_project` enforces
        the invariant; doing the deactivate first inside the same transaction
        keeps the constraint satisfied throughout.
        """
        await self._db.execute(
            t.search_keys.update()
            .where(
                sa.and_(
                    t.search_keys.c.project_id == project_id,
                    t.search_keys.c.id != key_id,
                    t.search_keys.c.is_active.is_(True),
                )
            )
            .values(is_active=False)
        )
        await self._db.execute(
            t.search_keys.update()
            .where(
                sa.and_(
                    t.search_keys.c.id == key_id,
                    t.search_keys.c.deleted_at.is_(None),
                )
            )
            .values(is_active=True)
        )

    async def soft_delete(self, key_id: uuid.UUID, *, at: Any) -> None:
        await self._db.execute(
            t.search_keys.update().where(t.search_keys.c.id == key_id).values(deleted_at=at, is_active=False)
        )


__all__ = ["SearchKeyRepository"]
