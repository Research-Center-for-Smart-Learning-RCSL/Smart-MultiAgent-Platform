"""Unit tests for InviteService and OCTransferService.

Covers: org/project invite creation + audit, accept by id, accept by token,
reject, expired-on-accept, email-not-for-caller guard, OC transfer
initiate/accept/cancel/admin-force, 7-day expiry, SEC-4 org-path mismatch,
demoted-target re-check.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.tenancy.application.invite_service import InviteService
from contexts.tenancy.application.oc_transfer_service import OCTransferService
from contexts.tenancy.domain.errors import (
    InviteExpired,
    InviteNotForCaller,
    InviteNotFound,
    OriginalCreatorConflict,
    TransferConflict,
    TransferNotFound,
)
from contexts.tenancy.domain.models import (
    Invite,
    InviteScope,
    InviteState,
    OCTransfer,
    OCTransferState,
    OrgMember,
    OrgMemberRole,
    ProjectMemberRole,
)

_NOW = datetime(2026, 6, 23, 12, 0, 0)
_USER = uuid.uuid4()
_TARGET = uuid.uuid4()
_ORG = uuid.uuid4()
_PROJECT = uuid.uuid4()
_INVITE = uuid.uuid4()
_TRANSFER = uuid.uuid4()


def _invite(
    *,
    invite_id: uuid.UUID | None = None,
    scope: InviteScope = InviteScope.ORG,
    scope_id: uuid.UUID | None = None,
    state: InviteState = InviteState.PENDING,
    invitee_email: str = "bob@example.com",
    expires_at: datetime | None = None,
) -> Invite:
    return Invite(
        id=invite_id or _INVITE,
        scope_type=scope,
        scope_id=scope_id or _ORG,
        role=OrgMemberRole.MEMBER.value,
        inviter_user_id=_USER,
        invitee_email=invitee_email,
        invitee_user_id=None,
        state=state,
        token_hash="hash",
        expires_at=expires_at or (_NOW + timedelta(days=7)),
        created_at=_NOW,
        resolved_at=None,
    )


def _transfer(
    *,
    transfer_id: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
    state: OCTransferState = OCTransferState.PENDING,
    expires_at: datetime | None = None,
) -> OCTransfer:
    return OCTransfer(
        id=transfer_id or _TRANSFER,
        org_id=org_id or _ORG,
        initiator_user_id=_USER,
        target_user_id=_TARGET,
        state=state,
        created_at=_NOW,
        expires_at=expires_at or (_NOW + timedelta(days=7)),
        resolved_at=None,
    )


def _org_member(
    *,
    user_id: uuid.UUID | None = None,
    role: OrgMemberRole = OrgMemberRole.OWNER,
    is_oc: bool = False,
) -> OrgMember:
    return OrgMember(
        org_id=_ORG,
        user_id=user_id or _USER,
        role=role,
        is_original_creator=is_oc,
        joined_at=_NOW,
    )


def _make_invite_service(
    *,
    invites: AsyncMock | None = None,
    org_members: AsyncMock | None = None,
    project_members: AsyncMock | None = None,
) -> InviteService:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(first=MagicMock(return_value=None)))
    svc = InviteService(db, email_sender=AsyncMock(), public_origin="http://localhost")
    if invites is not None:
        svc._invites = invites
    if org_members is not None:
        svc._org_members = org_members
    if project_members is not None:
        svc._project_members = project_members
    svc._identity = AsyncMock()
    return svc


def _make_transfer_service(
    *,
    transfers: AsyncMock | None = None,
    org_members: AsyncMock | None = None,
) -> OCTransferService:
    db = AsyncMock()
    svc = OCTransferService(db)
    if transfers is not None:
        svc._transfers = transfers
    if org_members is not None:
        svc._org_members = org_members
    return svc


# ===========================================================================
# InviteService
# ===========================================================================


class TestInviteCreateOrg:
    @patch("contexts.tenancy.application.invite_service.ratelimit.check_raw", new_callable=AsyncMock)
    @patch("contexts.tenancy.application.invite_service.audit.emit", new_callable=AsyncMock)
    async def test_creates_org_invite(self, _audit, _rl) -> None:
        _rl.return_value = MagicMock(allowed=True)
        inv = _invite()
        invites = AsyncMock()
        invites.create.return_value = ("plaintext-token", inv)
        svc = _make_invite_service(invites=invites)

        result = await svc.create_org_invite(
            org_id=_ORG,
            inviter_user_id=_USER,
            invitee_email="bob@example.com",
            actor_ip="1.2.3.4",
        )

        assert result.invite.id == inv.id
        assert result.plaintext_token == "plaintext-token"
        _audit.assert_awaited()
        assert _audit.call_args_list[0][0][1].action == "org.member_invited"


class TestInviteCreateProject:
    @patch("contexts.tenancy.application.invite_service.ratelimit.check_raw", new_callable=AsyncMock)
    @patch("contexts.tenancy.application.invite_service.audit.emit", new_callable=AsyncMock)
    async def test_creates_project_invite(self, _audit, _rl) -> None:
        _rl.return_value = MagicMock(allowed=True)
        inv = _invite(scope=InviteScope.PROJECT, scope_id=_PROJECT)
        invites = AsyncMock()
        invites.create.return_value = ("tok", inv)
        svc = _make_invite_service(invites=invites)

        result = await svc.create_project_invite(
            project_id=_PROJECT,
            inviter_user_id=_USER,
            invitee_email="bob@example.com",
            actor_ip=None,
        )

        assert result.invite.scope_type is InviteScope.PROJECT


class TestInviteAcceptById:
    @patch("contexts.tenancy.application.invite_service.now", return_value=_NOW)
    @patch("contexts.tenancy.application.invite_service.audit.emit", new_callable=AsyncMock)
    async def test_accept_org_invite(self, _audit, _now) -> None:
        inv = _invite()
        updated = _invite(state=InviteState.ACCEPTED)
        invites = AsyncMock()
        invites.get.return_value = inv
        invites.transition.return_value = updated
        org_members = AsyncMock()
        svc = _make_invite_service(invites=invites, org_members=org_members)
        scope_row = MagicMock()
        scope_row.first.return_value = MagicMock()
        svc._db.execute.return_value = scope_row

        result = await svc.accept(
            invite_id=_INVITE,
            caller_email="bob@example.com",
            caller_user_id=_TARGET,
            actor_ip=None,
        )

        assert result.state is InviteState.ACCEPTED
        org_members.add.assert_awaited_once()

    async def test_accept_wrong_email_raises(self) -> None:
        inv = _invite(invitee_email="bob@example.com")
        invites = AsyncMock()
        invites.get.return_value = inv
        svc = _make_invite_service(invites=invites)

        with pytest.raises(InviteNotForCaller):
            await svc.accept(
                invite_id=_INVITE,
                caller_email="alice@example.com",
                caller_user_id=_TARGET,
                actor_ip=None,
            )

    async def test_accept_not_found(self) -> None:
        invites = AsyncMock()
        invites.get.return_value = None
        svc = _make_invite_service(invites=invites)

        with pytest.raises(InviteNotFound):
            await svc.accept(
                invite_id=uuid.uuid4(),
                caller_email="bob@example.com",
                caller_user_id=_TARGET,
                actor_ip=None,
            )

    @patch("contexts.tenancy.application.invite_service.now", return_value=_NOW + timedelta(days=8))
    @patch("contexts.tenancy.application.invite_service.audit.emit", new_callable=AsyncMock)
    async def test_accept_expired(self, _audit, _now) -> None:
        inv = _invite(expires_at=_NOW + timedelta(days=7))
        invites = AsyncMock()
        invites.get.return_value = inv
        invites.transition.return_value = _invite(state=InviteState.EXPIRED)
        svc = _make_invite_service(invites=invites)

        with pytest.raises(InviteExpired):
            await svc.accept(
                invite_id=_INVITE,
                caller_email="bob@example.com",
                caller_user_id=_TARGET,
                actor_ip=None,
            )

        invites.transition.assert_awaited_once()
        transition_kwargs = invites.transition.call_args.kwargs
        assert transition_kwargs["new_state"] is InviteState.EXPIRED

    @patch("contexts.tenancy.application.invite_service.now", return_value=_NOW)
    @patch("contexts.tenancy.application.invite_service.audit.emit", new_callable=AsyncMock)
    async def test_accept_already_accepted_raises(self, _audit, _now) -> None:
        inv = _invite(state=InviteState.ACCEPTED)
        invites = AsyncMock()
        invites.get.return_value = inv
        svc = _make_invite_service(invites=invites)

        with pytest.raises(InviteNotFound):
            await svc.accept(
                invite_id=_INVITE,
                caller_email="bob@example.com",
                caller_user_id=_TARGET,
                actor_ip=None,
            )


class TestInviteAcceptByToken:
    @patch("contexts.tenancy.application.invite_service.now", return_value=_NOW)
    @patch("contexts.tenancy.application.invite_service.audit.emit", new_callable=AsyncMock)
    async def test_accept_by_token(self, _audit, _now) -> None:
        inv = _invite()
        updated = _invite(state=InviteState.ACCEPTED)
        invites = AsyncMock()
        invites.get_by_token.return_value = inv
        invites.transition.return_value = updated
        org_members = AsyncMock()
        svc = _make_invite_service(invites=invites, org_members=org_members)
        scope_row = MagicMock()
        scope_row.first.return_value = MagicMock()
        svc._db.execute.return_value = scope_row

        result = await svc.accept_by_token(
            token="plaintext-tok",
            caller_user_id=_TARGET,
            actor_ip=None,
        )

        assert result.state is InviteState.ACCEPTED

    async def test_token_not_found(self) -> None:
        invites = AsyncMock()
        invites.get_by_token.return_value = None
        svc = _make_invite_service(invites=invites)

        with pytest.raises(InviteNotFound):
            await svc.accept_by_token(
                token="bad-tok",
                caller_user_id=_TARGET,
                actor_ip=None,
            )


class TestInviteAcceptProject:
    @patch("contexts.tenancy.application.invite_service.now", return_value=_NOW)
    @patch("contexts.tenancy.application.invite_service.audit.emit", new_callable=AsyncMock)
    async def test_accept_project_invite(self, _audit, _now) -> None:
        inv = _invite(scope=InviteScope.PROJECT, scope_id=_PROJECT)
        updated = _invite(scope=InviteScope.PROJECT, scope_id=_PROJECT, state=InviteState.ACCEPTED)
        invites = AsyncMock()
        invites.get.return_value = inv
        invites.transition.return_value = updated
        project_members = AsyncMock()
        svc = _make_invite_service(invites=invites, project_members=project_members)
        scope_row = MagicMock()
        scope_row.first.return_value = MagicMock()
        svc._db.execute.return_value = scope_row

        result = await svc.accept(
            invite_id=_INVITE,
            caller_email="bob@example.com",
            caller_user_id=_TARGET,
            actor_ip=None,
        )

        project_members.add.assert_awaited_once()
        assert _audit.call_args_list[-1][0][1].action == "project.member_added"


class TestInviteReject:
    @patch("contexts.tenancy.application.invite_service.audit.emit", new_callable=AsyncMock)
    async def test_reject(self, _audit) -> None:
        inv = _invite()
        updated = _invite(state=InviteState.REJECTED)
        invites = AsyncMock()
        invites.get.return_value = inv
        invites.transition.return_value = updated
        svc = _make_invite_service(invites=invites)

        result = await svc.reject(
            invite_id=_INVITE,
            caller_email="bob@example.com",
            caller_user_id=_TARGET,
            actor_ip=None,
        )

        assert result.state is InviteState.REJECTED

    async def test_reject_wrong_email_raises(self) -> None:
        inv = _invite(invitee_email="bob@example.com")
        invites = AsyncMock()
        invites.get.return_value = inv
        svc = _make_invite_service(invites=invites)

        with pytest.raises(InviteNotForCaller):
            await svc.reject(
                invite_id=_INVITE,
                caller_email="alice@example.com",
                caller_user_id=_TARGET,
                actor_ip=None,
            )

    async def test_reject_not_pending_raises(self) -> None:
        inv = _invite(state=InviteState.ACCEPTED)
        invites = AsyncMock()
        invites.get.return_value = inv
        svc = _make_invite_service(invites=invites)

        with pytest.raises(InviteNotFound):
            await svc.reject(
                invite_id=_INVITE,
                caller_email="bob@example.com",
                caller_user_id=_TARGET,
                actor_ip=None,
            )


class TestInviteListInbound:
    async def test_list_inbound(self) -> None:
        invites = AsyncMock()
        invites.list_for_user.return_value = [_invite()]
        svc = _make_invite_service(invites=invites)

        result = await svc.list_inbound(
            caller_email="bob@example.com",
            caller_user_id=_USER,
        )

        assert len(result) == 1


# ===========================================================================
# OCTransferService
# ===========================================================================


class TestOCTransferInitiate:
    @patch("contexts.tenancy.application.oc_transfer_service.audit.emit", new_callable=AsyncMock)
    async def test_initiate_success(self, _audit) -> None:
        xfer = _transfer()
        transfers = AsyncMock()
        transfers.create.return_value = xfer
        org_members = AsyncMock()
        org_members.get.side_effect = [
            _org_member(user_id=_TARGET, role=OrgMemberRole.OWNER),
            _org_member(user_id=_USER, is_oc=True),
        ]
        svc = _make_transfer_service(transfers=transfers, org_members=org_members)

        result = await svc.initiate(
            org_id=_ORG,
            initiator_user_id=_USER,
            target_user_id=_TARGET,
            actor_ip=None,
        )

        assert result.id == xfer.id

    async def test_target_not_owner_raises(self) -> None:
        org_members = AsyncMock()
        org_members.get.return_value = _org_member(user_id=_TARGET, role=OrgMemberRole.MEMBER)
        svc = _make_transfer_service(org_members=org_members)

        with pytest.raises(TransferConflict, match="OrgOwner"):
            await svc.initiate(
                org_id=_ORG,
                initiator_user_id=_USER,
                target_user_id=_TARGET,
                actor_ip=None,
            )

    async def test_initiator_not_oc_raises(self) -> None:
        org_members = AsyncMock()
        org_members.get.side_effect = [
            _org_member(user_id=_TARGET, role=OrgMemberRole.OWNER),
            _org_member(user_id=_USER, is_oc=False),
        ]
        svc = _make_transfer_service(org_members=org_members)

        with pytest.raises(OriginalCreatorConflict):
            await svc.initiate(
                org_id=_ORG,
                initiator_user_id=_USER,
                target_user_id=_TARGET,
                actor_ip=None,
            )


class TestOCTransferAccept:
    @patch("contexts.tenancy.application.oc_transfer_service.now", return_value=_NOW)
    @patch("contexts.tenancy.application.oc_transfer_service.audit.emit", new_callable=AsyncMock)
    async def test_accept_success(self, _audit, _now) -> None:
        xfer = _transfer()
        updated = _transfer(state=OCTransferState.ACCEPTED)
        transfers = AsyncMock()
        transfers.get.return_value = xfer
        transfers.resolve.return_value = updated
        org_members = AsyncMock()
        org_members.get.side_effect = [
            _org_member(user_id=_TARGET, role=OrgMemberRole.OWNER),
            _org_member(user_id=_USER, is_oc=True),
        ]
        svc = _make_transfer_service(transfers=transfers, org_members=org_members)

        result = await svc.accept(
            org_id=_ORG,
            transfer_id=_TRANSFER,
            caller_user_id=_TARGET,
            actor_ip=None,
        )

        assert result.state is OCTransferState.ACCEPTED
        org_members.flip_original_creator.assert_awaited_once()

    async def test_accept_wrong_user_raises(self) -> None:
        xfer = _transfer()
        transfers = AsyncMock()
        transfers.get.return_value = xfer
        svc = _make_transfer_service(transfers=transfers)

        with pytest.raises(TransferConflict, match="only the target"):
            await svc.accept(
                org_id=_ORG,
                transfer_id=_TRANSFER,
                caller_user_id=uuid.uuid4(),
                actor_ip=None,
            )

    @patch("contexts.tenancy.application.oc_transfer_service.now")
    async def test_accept_expired_raises(self, _now) -> None:
        _now.return_value = _NOW + timedelta(days=8)
        xfer = _transfer(expires_at=_NOW + timedelta(days=7))
        transfers = AsyncMock()
        transfers.get.return_value = xfer
        svc = _make_transfer_service(transfers=transfers)

        with pytest.raises(TransferNotFound, match="expired"):
            await svc.accept(
                org_id=_ORG,
                transfer_id=_TRANSFER,
                caller_user_id=_TARGET,
                actor_ip=None,
            )

    async def test_accept_wrong_org_raises_sec4(self) -> None:
        wrong_org = uuid.uuid4()
        xfer = _transfer(org_id=_ORG)
        transfers = AsyncMock()
        transfers.get.return_value = xfer
        svc = _make_transfer_service(transfers=transfers)

        with pytest.raises(TransferNotFound):
            await svc.accept(
                org_id=wrong_org,
                transfer_id=_TRANSFER,
                caller_user_id=_TARGET,
                actor_ip=None,
            )

    async def test_accept_already_resolved_raises(self) -> None:
        xfer = _transfer()
        xfer = OCTransfer(
            id=_TRANSFER, org_id=_ORG, initiator_user_id=_USER,
            target_user_id=_TARGET, state=OCTransferState.ACCEPTED,
            created_at=_NOW, expires_at=_NOW + timedelta(days=7),
            resolved_at=_NOW,
        )
        transfers = AsyncMock()
        transfers.get.return_value = xfer
        svc = _make_transfer_service(transfers=transfers)

        with pytest.raises(TransferNotFound):
            await svc.accept(
                org_id=_ORG,
                transfer_id=_TRANSFER,
                caller_user_id=_TARGET,
                actor_ip=None,
            )

    @patch("contexts.tenancy.application.oc_transfer_service.now", return_value=_NOW)
    async def test_accept_target_demoted_raises(self, _now) -> None:
        xfer = _transfer()
        transfers = AsyncMock()
        transfers.get.return_value = xfer
        org_members = AsyncMock()
        org_members.get.return_value = _org_member(user_id=_TARGET, role=OrgMemberRole.MEMBER)
        svc = _make_transfer_service(transfers=transfers, org_members=org_members)

        with pytest.raises(TransferConflict, match="no longer an OrgOwner"):
            await svc.accept(
                org_id=_ORG,
                transfer_id=_TRANSFER,
                caller_user_id=_TARGET,
                actor_ip=None,
            )


class TestOCTransferCancel:
    @patch("contexts.tenancy.application.oc_transfer_service.audit.emit", new_callable=AsyncMock)
    async def test_cancel_by_initiator(self, _audit) -> None:
        xfer = _transfer()
        updated = _transfer(state=OCTransferState.CANCELLED)
        transfers = AsyncMock()
        transfers.get.return_value = xfer
        transfers.resolve.return_value = updated
        svc = _make_transfer_service(transfers=transfers)

        result = await svc.cancel(
            org_id=_ORG,
            transfer_id=_TRANSFER,
            caller_user_id=_USER,
            caller_is_admin=False,
            actor_ip=None,
        )

        assert result.state is OCTransferState.CANCELLED

    async def test_cancel_wrong_user_not_admin_raises(self) -> None:
        xfer = _transfer()
        transfers = AsyncMock()
        transfers.get.return_value = xfer
        svc = _make_transfer_service(transfers=transfers)

        with pytest.raises(TransferConflict, match="only the initiator"):
            await svc.cancel(
                org_id=_ORG,
                transfer_id=_TRANSFER,
                caller_user_id=uuid.uuid4(),
                caller_is_admin=False,
                actor_ip=None,
            )

    @patch("contexts.tenancy.application.oc_transfer_service.audit.emit", new_callable=AsyncMock)
    async def test_cancel_by_admin(self, _audit) -> None:
        xfer = _transfer()
        updated = _transfer(state=OCTransferState.CANCELLED)
        transfers = AsyncMock()
        transfers.get.return_value = xfer
        transfers.resolve.return_value = updated
        svc = _make_transfer_service(transfers=transfers)

        result = await svc.cancel(
            org_id=_ORG,
            transfer_id=_TRANSFER,
            caller_user_id=uuid.uuid4(),
            caller_is_admin=True,
            actor_ip=None,
        )

        assert result.state is OCTransferState.CANCELLED


class TestOCTransferAdminForce:
    @patch("contexts.tenancy.application.oc_transfer_service.audit.emit", new_callable=AsyncMock)
    async def test_admin_force(self, _audit) -> None:
        oc = _org_member(user_id=_USER, is_oc=True)
        target = _org_member(user_id=_TARGET, role=OrgMemberRole.OWNER)
        org_members = AsyncMock()
        org_members.original_creator.return_value = oc
        org_members.get.return_value = target
        svc = _make_transfer_service(org_members=org_members)

        admin = uuid.uuid4()
        await svc.admin_force(
            org_id=_ORG,
            target_user_id=_TARGET,
            admin_user_id=admin,
            actor_ip=None,
        )

        org_members.flip_original_creator.assert_awaited_once()
        assert _audit.call_args[0][1].action == "org.original_creator_force_transferred"

    async def test_admin_force_target_not_member_raises(self) -> None:
        org_members = AsyncMock()
        org_members.original_creator.return_value = _org_member(is_oc=True)
        org_members.get.return_value = None
        svc = _make_transfer_service(org_members=org_members)

        with pytest.raises(TransferConflict, match="not a member"):
            await svc.admin_force(
                org_id=_ORG,
                target_user_id=_TARGET,
                admin_user_id=uuid.uuid4(),
                actor_ip=None,
            )

    @patch("contexts.tenancy.application.oc_transfer_service.audit.emit", new_callable=AsyncMock)
    async def test_admin_force_promotes_member_to_owner(self, _audit) -> None:
        oc = _org_member(user_id=_USER, is_oc=True)
        target = _org_member(user_id=_TARGET, role=OrgMemberRole.MEMBER)
        org_members = AsyncMock()
        org_members.original_creator.return_value = oc
        org_members.get.return_value = target
        svc = _make_transfer_service(org_members=org_members)

        await svc.admin_force(
            org_id=_ORG,
            target_user_id=_TARGET,
            admin_user_id=uuid.uuid4(),
            actor_ip=None,
        )

        org_members.change_role.assert_awaited_once()
        change_kwargs = org_members.change_role.call_args.kwargs
        assert change_kwargs["new_role"] is OrgMemberRole.OWNER
