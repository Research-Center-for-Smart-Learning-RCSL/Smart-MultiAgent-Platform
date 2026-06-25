"""Original-Creator transfer protocol (§8.5, R8.15–R8.19)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.tenancy.domain.errors import (
    OriginalCreatorConflict,
    TransferConflict,
    TransferNotFound,
)
from contexts.tenancy.domain.models import (
    OCTransfer,
    OCTransferState,
    OrgMemberRole,
)
from contexts.tenancy.infrastructure.repositories import (
    OCTransferRepository,
    OrgMemberRepository,
)
from shared_kernel import audit
from shared_kernel.auth.clients import now


class OCTransferService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._transfers = OCTransferRepository(db)
        self._org_members = OrgMemberRepository(db)

    async def initiate(
        self,
        *,
        org_id: uuid.UUID,
        initiator_user_id: uuid.UUID,
        target_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> OCTransfer:
        # R8.15: target must already be an OrgOwner.
        target_member = await self._org_members.get(
            org_id=org_id,
            user_id=target_user_id,
        )
        if target_member is None or target_member.role is not OrgMemberRole.OWNER:
            raise TransferConflict("target must already be an OrgOwner")
        # R8.03: initiator must be the current OC.
        if not await self._assert_is_oc(org_id, initiator_user_id):
            raise OriginalCreatorConflict("only the Original Creator may transfer")

        transfer = await self._transfers.create(
            org_id=org_id,
            initiator_user_id=initiator_user_id,
            target_user_id=target_user_id,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="org.original_creator_transfer_initiated",
                actor_user_id=initiator_user_id,
                actor_ip=actor_ip,
                resource_type="org",
                resource_id=org_id,
                metadata={"target_user_id": str(target_user_id), "transfer_id": str(transfer.id)},
                request_id=request_id,
            ),
        )
        return transfer

    async def accept(
        self,
        *,
        org_id: uuid.UUID,
        transfer_id: uuid.UUID,
        caller_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> OCTransfer:
        transfer = await self._transfers.get(transfer_id)
        if transfer is None or transfer.resolved_at is not None:
            raise TransferNotFound(str(transfer_id))
        # The transfer must belong to the org named in the request path —
        # otherwise a member of org A could resolve a transfer in org B by
        # quoting B's transfer id under A's path (SEC-4). A mismatch is
        # reported as "not found" so the path cannot be used to probe.
        if transfer.org_id != org_id:
            raise TransferNotFound(str(transfer_id))
        if transfer.target_user_id != caller_user_id:
            raise TransferConflict("only the target may accept")
        if transfer.expires_at < now():
            await self._transfers.resolve(transfer_id, OCTransferState.EXPIRED)
            raise TransferNotFound(f"transfer {transfer_id} has expired")

        # R8.15 re-check at accept time: target may have been demoted /
        # kicked during the 7-day window. Also re-confirm the initiator still
        # holds the OC bit — an Admin force-transfer could have swapped it out.
        target_member = await self._org_members.get(
            org_id=transfer.org_id,
            user_id=transfer.target_user_id,
        )
        if target_member is None or target_member.role is not OrgMemberRole.OWNER:
            raise TransferConflict("target is no longer an OrgOwner")
        initiator_member = await self._org_members.get(
            org_id=transfer.org_id,
            user_id=transfer.initiator_user_id,
        )
        if initiator_member is None or not initiator_member.is_original_creator:
            raise TransferConflict("initiator is no longer the Original Creator")

        updated = await self._transfers.resolve(transfer_id, OCTransferState.ACCEPTED)
        if updated is None:
            raise TransferNotFound(str(transfer_id))
        # Atomically flip the OC bit.
        await self._org_members.flip_original_creator(
            org_id=transfer.org_id,
            from_user_id=transfer.initiator_user_id,
            to_user_id=transfer.target_user_id,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="org.original_creator_transferred",
                actor_user_id=caller_user_id,
                actor_ip=actor_ip,
                resource_type="org",
                resource_id=transfer.org_id,
                metadata={
                    "from_user_id": str(transfer.initiator_user_id),
                    "to_user_id": str(transfer.target_user_id),
                    "transfer_id": str(transfer.id),
                },
                request_id=request_id,
            ),
        )
        return updated

    async def cancel(
        self,
        *,
        org_id: uuid.UUID,
        transfer_id: uuid.UUID,
        caller_user_id: uuid.UUID,
        caller_is_admin: bool,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> OCTransfer:
        transfer = await self._transfers.get(transfer_id)
        if transfer is None or transfer.resolved_at is not None:
            raise TransferNotFound(str(transfer_id))
        # The transfer must belong to the org named in the request path (SEC-4).
        if transfer.org_id != org_id:
            raise TransferNotFound(str(transfer_id))
        if not caller_is_admin and transfer.initiator_user_id != caller_user_id:
            raise TransferConflict("only the initiator or an admin may cancel")
        updated = await self._transfers.resolve(transfer_id, OCTransferState.CANCELLED)
        if updated is None:
            raise TransferNotFound(str(transfer_id))
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="org.original_creator_transfer_cancelled",
                actor_user_id=caller_user_id,
                actor_ip=actor_ip,
                resource_type="org",
                resource_id=transfer.org_id,
                metadata={"transfer_id": str(transfer.id)},
                request_id=request_id,
            ),
        )
        return updated

    async def reject(
        self,
        *,
        org_id: uuid.UUID,
        transfer_id: uuid.UUID,
        caller_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> OCTransfer:
        transfer = await self._transfers.get(transfer_id)
        if transfer is None or transfer.resolved_at is not None:
            raise TransferNotFound(str(transfer_id))
        if transfer.org_id != org_id:
            raise TransferNotFound(str(transfer_id))
        if transfer.target_user_id != caller_user_id:
            raise TransferConflict("only the target may reject")
        updated = await self._transfers.resolve(transfer_id, OCTransferState.REJECTED)
        if updated is None:
            raise TransferNotFound(str(transfer_id))
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="org.original_creator_transfer_rejected",
                actor_user_id=caller_user_id,
                actor_ip=actor_ip,
                resource_type="org",
                resource_id=transfer.org_id,
                metadata={"transfer_id": str(transfer.id)},
                request_id=request_id,
            ),
        )
        return updated

    async def list_pending(self, org_id: uuid.UUID) -> Sequence[OCTransfer]:
        return await self._transfers.list_pending(org_id)

    async def admin_force(
        self,
        *,
        org_id: uuid.UUID,
        target_user_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        """R8.19 — Admin force-transfer, consent bypassed. Audit is critical."""
        oc = await self._org_members.original_creator(org_id)
        target = await self._org_members.get(org_id=org_id, user_id=target_user_id)
        if target is None:
            raise TransferConflict("target is not a member of the org")
        if target.role is not OrgMemberRole.OWNER:
            await self._org_members.change_role(
                org_id=org_id,
                user_id=target_user_id,
                new_role=OrgMemberRole.OWNER,
            )
        # When there is no current OC (edge case: admin cleared via direct DB
        # or previous forced-transfer left an inconsistent state), pass
        # target_user_id as both sides so the single-UPDATE CASE sets the bit
        # on the target row without touching any other row.
        from_id = oc.user_id if oc is not None else target_user_id
        await self._org_members.flip_original_creator(
            org_id=org_id,
            from_user_id=from_id,
            to_user_id=target_user_id,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="org.original_creator_force_transferred",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="org",
                resource_id=org_id,
                metadata={
                    "to_user_id": str(target_user_id),
                    "from_user_id": str(oc.user_id) if oc else None,
                },
                request_id=request_id,
            ),
        )

    async def _assert_is_oc(self, org_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        member = await self._org_members.get(org_id=org_id, user_id=user_id)
        return member is not None and member.is_original_creator


__all__ = ["OCTransferService"]
