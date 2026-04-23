"""Individual-key use cases — upload, retest, delete, list (D.4 / §22.4).

The service owns the *orchestration* of the three independent adapters the
surface needs (probe → envelope → persist + audit). Moving any one of them
inline in the HTTP handler would collapse the SoC that §R7.02–R7.15 rely on:
the probe must happen before the secret is enveloped, the envelope must
happen before the row hits the DB, and the audit row must share the same
unit of work so a rollback cleans up both.

SoC boundaries:
- No FastAPI imports. Handlers translate DTOs and call these methods.
- No httpx imports (probes live behind `contexts.keys.infrastructure.probes`).
- No Vault imports (sealing goes through `shared_kernel.security.envelope`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.domain.errors import KeyNotFound, KeyNotOwnedByCaller
from contexts.keys.domain.models import ApiKey, mask_preview
from contexts.keys.domain.providers import ApiKeyProvider
from contexts.keys.infrastructure.probes import ProbeStatus, probe
from contexts.keys.infrastructure.repositories import ApiKeyRepository
from contexts.notification.application.notification_service import NotificationService
from contexts.notification.domain.models import NotificationKind
from shared_kernel import audit
from shared_kernel.events.key_revocation import publish_key_revoked
from shared_kernel.security import envelope as env


@dataclass(frozen=True, slots=True)
class UploadedKey:
    """Return shape for `POST /api/keys`.

    Deliberately contains no plaintext and no envelope fields — the router
    translates this into the masked-only response body.
    """

    key: ApiKey


class KeyService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = ApiKeyRepository(db)

    async def list_owned(self, owner_user_id: uuid.UUID) -> list[ApiKey]:
        return await self._repo.list_owned(owner_user_id)

    async def upload(
        self,
        *,
        owner_user_id: uuid.UUID,
        provider: ApiKeyProvider,
        name: str,
        secret: str,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> UploadedKey:
        """Run the §7.2 upload flow.

        Order is load-bearing — we probe *before* persisting so a live-invalid
        secret never even touches the envelope layer. `test_status='failed'`
        cases still get persisted per R7.3 so the user can fix the name/label
        and retest without re-pasting the secret.
        """
        probe_result = await probe(provider, secret)

        # Generate the id client-side so AAD and the insert row agree without
        # a second Vault round-trip (R7.06 step 3 — AAD bound to logical id).
        key_id = uuid.uuid4()
        record = env.encrypt_envelope(secret.encode("utf-8"), env.api_key_aad(key_id))

        preview = mask_preview(secret)
        # Zeroise the caller's plaintext binding. Python cannot enforce scrub
        # of the underlying string, but rebinding the name prevents accidental
        # downstream reuse.
        secret = ""  # noqa: F841

        now = datetime.now(tz=UTC)
        key = await self._repo.insert(
            key_id=key_id,
            owner_user_id=owner_user_id,
            provider=provider,
            name=name,
            envelope=record,
            masked_preview=preview,
            test_status=probe_result.status,
            test_error=probe_result.error,
            last_test_at=now,
        )

        # `key.uploaded` is the *persistence* event — it fires regardless of
        # probe outcome so the audit trail preserves the "secret landed at T"
        # signal even when the live test later fails. `key.test_success` /
        # `key.test_failed` report the probe outcome as a separate, queryable
        # event (cross-cutting checklist item 2).
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="key.uploaded",
                actor_user_id=owner_user_id,
                actor_ip=actor_ip,
                resource_type="api_key",
                resource_id=key.id,
                metadata={"provider": provider.value},
                request_id=request_id,
            ),
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=(
                    "key.test_success" if probe_result.status is ProbeStatus.OK
                    else "key.test_failed"
                ),
                actor_user_id=owner_user_id,
                actor_ip=actor_ip,
                resource_type="api_key",
                resource_id=key.id,
                metadata={
                    "provider": provider.value,
                    "test_status": probe_result.status.value,
                    "test_error": probe_result.error,
                },
                request_id=request_id,
            ),
        )
        if probe_result.status is not ProbeStatus.OK:
            await NotificationService(self._db).send(
                user_id=owner_user_id,
                kind=NotificationKind.KEY_TEST_FAILED,
                title="API key test failed",
                body=probe_result.error,
                metadata={"key_id": str(key.id), "provider": provider.value},
            )
        return UploadedKey(key=key)

    async def retest(
        self,
        *,
        key_id: uuid.UUID,
        caller_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> ApiKey:
        """Re-run the live probe (`POST /api/keys/{id}/retest`, §22.4)."""
        loaded = await self._repo.get_active_with_envelope(key_id)
        if loaded is None:
            raise KeyNotFound(str(key_id))
        key, record = loaded
        if key.owner_user_id != caller_user_id:
            # Non-owners cannot retest — they cannot see the plaintext and
            # therefore cannot reprove it. Revocation happens via DELETE.
            raise KeyNotOwnedByCaller(str(key_id))

        plaintext = env.decrypt_envelope(record, env.api_key_aad(key_id))
        try:
            result = await probe(key.provider, plaintext.decode("utf-8"))
        finally:
            plaintext = b"\x00" * len(plaintext)  # best-effort scrub

        now = datetime.now(tz=UTC)
        await self._repo.update_probe_result(
            key_id=key_id,
            test_status=result.status,
            test_error=result.error,
            last_test_at=now,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=(
                    "key.test_success" if result.status is ProbeStatus.OK
                    else "key.test_failed"
                ),
                actor_user_id=caller_user_id,
                actor_ip=actor_ip,
                resource_type="api_key",
                resource_id=key_id,
                metadata={
                    "provider": key.provider.value,
                    "test_status": result.status.value,
                    "test_error": result.error,
                },
                request_id=request_id,
            ),
        )
        if result.status is not ProbeStatus.OK:
            await NotificationService(self._db).send(
                user_id=caller_user_id,
                kind=NotificationKind.KEY_TEST_FAILED,
                title="API key retest failed",
                body=result.error,
                metadata={"key_id": str(key_id), "provider": key.provider.value},
            )
        refreshed = await self._repo.get_active(key_id)
        assert refreshed is not None  # row was active a moment ago
        return refreshed

    async def delete(
        self,
        *,
        key_id: uuid.UUID,
        caller_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        """Soft-delete `key_id` (`DELETE /api/keys/{id}`, §22.4).

        Hard delete is reserved for the `user.deleted` cascade. Soft delete
        preserves audit traceability and lets the D.10 rotation walker
        distinguish "legitimately removed" from "orphan row".
        """
        key = await self._repo.get_active(key_id)
        if key is None:
            raise KeyNotFound(str(key_id))
        if key.owner_user_id != caller_user_id:
            raise KeyNotOwnedByCaller(str(key_id))

        now = datetime.now(tz=UTC)
        await self._repo.soft_delete(key_id, at=now)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="key.deleted",
                actor_user_id=caller_user_id,
                actor_ip=actor_ip,
                resource_type="api_key",
                resource_id=key_id,
                metadata={"provider": key.provider.value},
                request_id=request_id,
            ),
        )
        # Fanout to every app worker's in-process DEK cache (D.7 consumer).
        await publish_key_revoked(key_id)


__all__ = ["KeyService", "UploadedKey"]
