"""Repositories for the keys context.

Each method targets one Table and returns domain dataclasses — no
``select(*)`` joins across contexts (R23.01). Envelope rows are returned as
a `(ApiKey, EnvelopeRecord)` tuple when the caller needs to decrypt so the
domain model stays plaintext-free.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.domain.models import ApiKey
from contexts.keys.domain.providers import ApiKeyProvider
from contexts.keys.infrastructure import tables as t
from contexts.keys.infrastructure.probes.base import ProbeStatus
from shared_kernel.infra.vault import EnvelopeRecord


def _row_to_api_key(row: Any) -> ApiKey:
    return ApiKey(
        id=row.id,
        owner_user_id=row.owner_user_id,
        provider=ApiKeyProvider(row.provider),
        name=row.name,
        masked_preview=row.masked_preview,
        test_status=ProbeStatus(row.test_status),
        test_error=row.test_error,
        last_test_at=row.last_test_at,
        transit_key_version=row.transit_key_version,
        hmac_key_version=row.hmac_key_version,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


def _row_to_envelope(row: Any) -> EnvelopeRecord:
    return EnvelopeRecord(
        ciphertext=bytes(row.ciphertext),
        nonce=bytes(row.nonce),
        dek_wrapped=row.dek_wrapped,
        ciphertext_hmac=bytes(row.ciphertext_hmac),
        transit_key_version=row.transit_key_version,
        hmac_key_version=row.hmac_key_version,
    )


class ApiKeyRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def insert(
        self,
        *,
        key_id: uuid.UUID,
        owner_user_id: uuid.UUID,
        provider: ApiKeyProvider,
        name: str,
        envelope: EnvelopeRecord,
        masked_preview: str,
        test_status: ProbeStatus,
        test_error: str | None,
        last_test_at: Any,
    ) -> ApiKey:
        if not envelope.ciphertext or not envelope.nonce or not envelope.ciphertext_hmac:
            raise ValueError("envelope has empty ciphertext/nonce/hmac; refusing to persist corrupted key material")
        if not envelope.dek_wrapped:
            raise ValueError("envelope has empty dek_wrapped; refusing to persist corrupted key material")
        if envelope.transit_key_version < 1 or envelope.hmac_key_version < 1:
            raise ValueError("envelope has invalid key version; refusing to persist corrupted key material")
        stmt = (
            t.api_keys.insert()
            .values(
                id=key_id,
                owner_user_id=owner_user_id,
                provider=provider.value,
                name=name,
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
            )
            .returning(t.api_keys)
        )
        row = (await self._db.execute(stmt)).one()
        return _row_to_api_key(row)

    async def get_active(self, key_id: uuid.UUID) -> ApiKey | None:
        row = (
            await self._db.execute(
                t.api_keys.select().where(
                    sa.and_(
                        t.api_keys.c.id == key_id,
                        t.api_keys.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        return _row_to_api_key(row) if row else None

    async def get_active_with_envelope(
        self, key_id: uuid.UUID
    ) -> tuple[ApiKey, EnvelopeRecord] | None:
        row = (
            await self._db.execute(
                t.api_keys.select().where(
                    sa.and_(
                        t.api_keys.c.id == key_id,
                        t.api_keys.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        if row is None:
            return None
        return _row_to_api_key(row), _row_to_envelope(row)

    async def list_owned(self, owner_user_id: uuid.UUID) -> list[ApiKey]:
        rows = (
            await self._db.execute(
                t.api_keys.select()
                .where(
                    sa.and_(
                        t.api_keys.c.owner_user_id == owner_user_id,
                        t.api_keys.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.api_keys.c.created_at.desc())
            )
        ).all()
        return [_row_to_api_key(r) for r in rows]

    async def update_probe_result(
        self,
        *,
        key_id: uuid.UUID,
        test_status: ProbeStatus,
        test_error: str | None,
        last_test_at: Any,
    ) -> None:
        await self._db.execute(
            t.api_keys.update()
            .where(
                sa.and_(
                    t.api_keys.c.id == key_id,
                    t.api_keys.c.deleted_at.is_(None),
                )
            )
            .values(
                test_status=test_status.value,
                test_error=test_error,
                last_test_at=last_test_at,
            )
        )

    async def soft_delete(self, key_id: uuid.UUID, *, at: Any) -> None:
        await self._db.execute(
            t.api_keys.update()
            .where(
                sa.and_(
                    t.api_keys.c.id == key_id,
                    t.api_keys.c.deleted_at.is_(None),
                )
            )
            .values(deleted_at=at)
        )


__all__ = ["ApiKeyRepository"]
