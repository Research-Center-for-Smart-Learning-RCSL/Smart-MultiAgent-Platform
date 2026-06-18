"""Tenancy repositories. All cross-context data comes via the IdentityFacade.

No SQL joins reach into `users` except via FK existence — that's a row read,
not a join. This preserves the §23 no-cross-context-joins rule.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.tenancy.domain.errors import (
    InviteDuplicate,
    NameTaken,
    OriginalCreatorConflict,
    TransferConflict,
    VersionMismatch,
)
from contexts.tenancy.domain.models import (
    Invite,
    InviteScope,
    InviteState,
    OCTransfer,
    OCTransferState,
    Org,
    OrgMember,
    OrgMemberRole,
    Project,
    ProjectMember,
    ProjectMemberRole,
)
from contexts.tenancy.infrastructure import tables as t
from shared_kernel.auth.clients import now

# ---------- Org ------------------------------------------------------------


def _row_to_org(row: Any) -> Org:
    return Org(
        id=row.id,
        name=row.name,
        creator_user_id=row.creator_user_id,
        version=row.version,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
    )


class OrgRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(self, *, name: str, creator_user_id: uuid.UUID) -> Org:
        try:
            row = (
                await self._db.execute(
                    t.orgs.insert().values(name=name, creator_user_id=creator_user_id).returning(t.orgs)
                )
            ).one()
        except IntegrityError as exc:
            raise NameTaken(name) from exc
        return _row_to_org(row)

    async def get(self, org_id: uuid.UUID, *, include_deleted: bool = False) -> Org | None:
        predicate = t.orgs.c.id == org_id
        if not include_deleted:
            predicate = sa.and_(predicate, t.orgs.c.deleted_at.is_(None))
        row = (await self._db.execute(t.orgs.select().where(predicate))).first()
        return _row_to_org(row) if row else None

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[Org]:
        rows = (
            await self._db.execute(
                t.orgs.select()
                .select_from(t.orgs.join(t.org_members, t.org_members.c.org_id == t.orgs.c.id))
                .where(
                    sa.and_(
                        t.org_members.c.user_id == user_id,
                        t.orgs.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.orgs.c.created_at.desc())
            )
        ).all()
        return [_row_to_org(r) for r in rows]

    async def rename(self, *, org_id: uuid.UUID, new_name: str, expected_version: int) -> Org:
        stmt = (
            t.orgs.update()
            .where(
                sa.and_(
                    t.orgs.c.id == org_id,
                    t.orgs.c.version == expected_version,
                    t.orgs.c.deleted_at.is_(None),
                )
            )
            .values(name=new_name)
            .returning(t.orgs)
        )
        try:
            row = (await self._db.execute(stmt)).first()
        except IntegrityError as exc:
            raise NameTaken(new_name) from exc
        if row is None:
            raise VersionMismatch(f"org {org_id} version mismatch or missing")
        return _row_to_org(row)

    async def soft_delete(self, org_id: uuid.UUID) -> None:
        await self._db.execute(t.orgs.update().where(t.orgs.c.id == org_id).values(deleted_at=now()))

    async def restore(self, org_id: uuid.UUID) -> None:
        await self._db.execute(t.orgs.update().where(t.orgs.c.id == org_id).values(deleted_at=None))


# ---------- OrgMember ------------------------------------------------------


class OrgMemberRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(
        self,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        role: OrgMemberRole,
        is_original_creator: bool = False,
    ) -> None:
        try:
            await self._db.execute(
                t.org_members.insert().values(
                    org_id=org_id,
                    user_id=user_id,
                    role=role.value,
                    is_original_creator=is_original_creator,
                )
            )
        except IntegrityError as exc:
            # EXCLUDE constraint for OC or PK duplicate.
            msg = str(exc.orig or exc).lower()
            if "ex_org_members_one_oc" in msg:
                raise OriginalCreatorConflict("another OC already exists") from exc
            raise

    async def get(self, *, org_id: uuid.UUID, user_id: uuid.UUID) -> OrgMember | None:
        row = (
            await self._db.execute(
                t.org_members.select().where(
                    sa.and_(
                        t.org_members.c.org_id == org_id,
                        t.org_members.c.user_id == user_id,
                    )
                )
            )
        ).first()
        return _row_to_org_member(row) if row else None

    async def list(self, org_id: uuid.UUID) -> Sequence[OrgMember]:
        rows = (
            await self._db.execute(
                t.org_members.select()
                .where(t.org_members.c.org_id == org_id)
                .order_by(t.org_members.c.joined_at)
            )
        ).all()
        return [_row_to_org_member(r) for r in rows]

    async def remove(self, *, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self._db.execute(
            t.org_members.delete().where(
                sa.and_(
                    t.org_members.c.org_id == org_id,
                    t.org_members.c.user_id == user_id,
                    sa.not_(t.org_members.c.is_original_creator),
                )
            )
        )

    async def change_role(self, *, org_id: uuid.UUID, user_id: uuid.UUID, new_role: OrgMemberRole) -> int:
        result = await self._db.execute(
            t.org_members.update()
            .where(
                sa.and_(
                    t.org_members.c.org_id == org_id,
                    t.org_members.c.user_id == user_id,
                    sa.not_(t.org_members.c.is_original_creator),
                )
            )
            .values(role=new_role.value)
        )
        return result.rowcount or 0

    async def flip_original_creator(
        self,
        *,
        org_id: uuid.UUID,
        from_user_id: uuid.UUID,
        to_user_id: uuid.UUID,
    ) -> None:
        """Atomically move the OC bit from one row to another within a transaction.

        Two separate UPDATE statements: clear first, then set. This avoids any
        reliance on when the EXCLUDE constraint is evaluated for a multi-row
        UPDATE — the constraint is satisfied after step 1 completes, before
        step 2 runs.
        """
        await self._db.execute(
            t.org_members.update()
            .where(
                sa.and_(
                    t.org_members.c.org_id == org_id,
                    t.org_members.c.user_id == from_user_id,
                )
            )
            .values(is_original_creator=False)
        )
        await self._db.execute(
            t.org_members.update()
            .where(
                sa.and_(
                    t.org_members.c.org_id == org_id,
                    t.org_members.c.user_id == to_user_id,
                )
            )
            .values(is_original_creator=True, role=OrgMemberRole.OWNER.value)
        )

    async def original_creator(self, org_id: uuid.UUID) -> OrgMember | None:
        row = (
            await self._db.execute(
                t.org_members.select().where(
                    sa.and_(
                        t.org_members.c.org_id == org_id,
                        t.org_members.c.is_original_creator.is_(True),
                    )
                )
            )
        ).first()
        return _row_to_org_member(row) if row else None

    async def count_active_members(self, org_id: uuid.UUID) -> int:
        row = (
            await self._db.execute(
                sa.select(sa.func.count()).select_from(t.org_members).where(t.org_members.c.org_id == org_id)
            )
        ).one()
        return int(row[0])


def _row_to_org_member(row: Any) -> OrgMember:
    return OrgMember(
        org_id=row.org_id,
        user_id=row.user_id,
        role=OrgMemberRole(row.role),
        is_original_creator=row.is_original_creator,
        joined_at=row.joined_at,
    )


# ---------- Project -------------------------------------------------------


def _row_to_project(row: Any) -> Project:
    return Project(
        id=row.id,
        owner_user_id=row.owner_user_id,
        owner_org_id=row.owner_org_id,
        name=row.name,
        created_by_user_id=row.created_by_user_id,
        version=row.version,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
    )


class ProjectRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        name: str,
        owner_user_id: uuid.UUID | None,
        owner_org_id: uuid.UUID | None,
        created_by_user_id: uuid.UUID,
    ) -> Project:
        try:
            row = (
                await self._db.execute(
                    t.projects.insert()
                    .values(
                        name=name,
                        owner_user_id=owner_user_id,
                        owner_org_id=owner_org_id,
                        created_by_user_id=created_by_user_id,
                    )
                    .returning(t.projects)
                )
            ).one()
        except IntegrityError as exc:
            raise NameTaken(name) from exc
        return _row_to_project(row)

    async def get(self, project_id: uuid.UUID, *, include_deleted: bool = False) -> Project | None:
        predicate = t.projects.c.id == project_id
        if not include_deleted:
            predicate = sa.and_(predicate, t.projects.c.deleted_at.is_(None))
        row = (await self._db.execute(t.projects.select().where(predicate))).first()
        return _row_to_project(row) if row else None

    async def list_by_user(self, user_id: uuid.UUID) -> Sequence[Project]:
        rows = (
            await self._db.execute(
                t.projects.select().where(
                    sa.and_(
                        t.projects.c.owner_user_id == user_id,
                        t.projects.c.deleted_at.is_(None),
                    )
                )
            )
        ).all()
        return [_row_to_project(r) for r in rows]

    async def list_by_org(self, org_id: uuid.UUID) -> Sequence[Project]:
        rows = (
            await self._db.execute(
                t.projects.select().where(
                    sa.and_(
                        t.projects.c.owner_org_id == org_id,
                        t.projects.c.deleted_at.is_(None),
                    )
                )
            )
        ).all()
        return [_row_to_project(r) for r in rows]

    async def rename(self, *, project_id: uuid.UUID, new_name: str, expected_version: int) -> Project:
        stmt = (
            t.projects.update()
            .where(
                sa.and_(
                    t.projects.c.id == project_id,
                    t.projects.c.version == expected_version,
                    t.projects.c.deleted_at.is_(None),
                )
            )
            .values(name=new_name)
            .returning(t.projects)
        )
        try:
            row = (await self._db.execute(stmt)).first()
        except IntegrityError as exc:
            raise NameTaken(new_name) from exc
        if row is None:
            raise VersionMismatch(f"project {project_id} version mismatch or missing")
        return _row_to_project(row)

    async def soft_delete(self, project_id: uuid.UUID) -> None:
        await self._db.execute(
            t.projects.update().where(t.projects.c.id == project_id).values(deleted_at=now())
        )

    async def restore(self, project_id: uuid.UUID) -> None:
        await self._db.execute(
            t.projects.update().where(t.projects.c.id == project_id).values(deleted_at=None)
        )


class ProjectMemberRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        role: ProjectMemberRole,
    ) -> None:
        await self._db.execute(
            t.project_members.insert().values(
                project_id=project_id,
                user_id=user_id,
                role=role.value,
            )
        )

    async def list(self, project_id: uuid.UUID) -> Sequence[ProjectMember]:
        rows = (
            await self._db.execute(
                t.project_members.select().where(t.project_members.c.project_id == project_id)
            )
        ).all()
        return [
            ProjectMember(
                project_id=r.project_id,
                user_id=r.user_id,
                role=ProjectMemberRole(r.role),
                joined_at=r.joined_at,
            )
            for r in rows
        ]

    async def list_project_ids_for_user(self, user_id: uuid.UUID) -> Sequence[uuid.UUID]:
        """Every project the user holds a membership row in (any role).

        Used by the account-self-deletion cascade (R8.14) to find the projects
        the user must be removed from; ownership is a separate axis handled via
        ``ProjectRepository.list_by_user``.

        Returns a ``Sequence`` rather than ``list[...]``: under PEP 563 a
        ``list[...]`` annotation in this class resolves to the sibling ``list``
        method, not the builtin (the same footgun that bit ``message_service``).
        """
        rows = (
            await self._db.execute(
                sa.select(t.project_members.c.project_id).where(t.project_members.c.user_id == user_id)
            )
        ).all()
        return [r.project_id for r in rows]

    async def get(self, *, project_id: uuid.UUID, user_id: uuid.UUID) -> ProjectMember | None:
        row = (
            await self._db.execute(
                t.project_members.select().where(
                    sa.and_(
                        t.project_members.c.project_id == project_id,
                        t.project_members.c.user_id == user_id,
                    )
                )
            )
        ).first()
        if row is None:
            return None
        return ProjectMember(
            project_id=row.project_id,
            user_id=row.user_id,
            role=ProjectMemberRole(row.role),
            joined_at=row.joined_at,
        )

    async def remove(self, *, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self._db.execute(
            t.project_members.delete().where(
                sa.and_(
                    t.project_members.c.project_id == project_id,
                    t.project_members.c.user_id == user_id,
                )
            )
        )

    async def change_role(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID, new_role: ProjectMemberRole
    ) -> int:
        result = await self._db.execute(
            t.project_members.update()
            .where(
                sa.and_(
                    t.project_members.c.project_id == project_id,
                    t.project_members.c.user_id == user_id,
                )
            )
            .values(role=new_role.value)
        )
        return result.rowcount or 0


# ---------- Invite --------------------------------------------------------


def _hash_token(token: str) -> str:
    # utf-8 (not ascii) so a non-ASCII token never raises UnicodeEncodeError →
    # an invalid token simply hashes to a non-match (404), which is correct.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


class InviteRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        scope_type: InviteScope,
        scope_id: uuid.UUID,
        role: str,
        inviter_user_id: uuid.UUID,
        invitee_email: str,
        invitee_user_id: uuid.UUID | None,
        ttl: timedelta = timedelta(days=7),
    ) -> tuple[str, Invite]:
        token = _new_token()
        token_hash = _hash_token(token)
        try:
            row = (
                await self._db.execute(
                    t.invites.insert()
                    .values(
                        scope_type=scope_type.value,
                        scope_id=scope_id,
                        role=role,
                        inviter_user_id=inviter_user_id,
                        invitee_email=invitee_email,
                        invitee_user_id=invitee_user_id,
                        token_hash=token_hash,
                        expires_at=now() + ttl,
                    )
                    .returning(t.invites)
                )
            ).one()
        except IntegrityError as exc:
            raise InviteDuplicate(
                f"duplicate pending invite for {invitee_email} in {scope_type.value}:{scope_id}"
            ) from exc
        return token, _row_to_invite(row)

    async def list_for_user(
        self,
        *,
        email: str,
        user_id: uuid.UUID | None,
        states: Sequence[InviteState] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Invite]:
        predicate: sa.ColumnElement[bool] = sa.or_(
            t.invites.c.invitee_email == email,
            t.invites.c.invitee_user_id == user_id,
        )
        if states:
            predicate = sa.and_(predicate, t.invites.c.state.in_([s.value for s in states]))
        rows = (
            await self._db.execute(
                t.invites.select()
                .where(predicate)
                .order_by(t.invites.c.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [_row_to_invite(r) for r in rows]

    async def get(self, invite_id: uuid.UUID) -> Invite | None:
        row = (await self._db.execute(t.invites.select().where(t.invites.c.id == invite_id))).first()
        return _row_to_invite(row) if row else None

    async def get_by_token(self, token: str) -> Invite | None:
        """Resolve an invite by its plaintext token (hash lookup, R6.09).

        Possession of the token is the authorisation for the accept-by-link
        path — the token is never persisted in plaintext, only its SHA-256.
        """
        token_hash = _hash_token(token)
        row = (await self._db.execute(t.invites.select().where(t.invites.c.token_hash == token_hash))).first()
        return _row_to_invite(row) if row else None

    async def transition(
        self,
        *,
        invite_id: uuid.UUID,
        new_state: InviteState,
        invitee_user_id: uuid.UUID | None = None,
    ) -> Invite | None:
        values: dict[str, Any] = {
            "state": new_state.value,
            "resolved_at": now(),
        }
        if invitee_user_id is not None:
            values["invitee_user_id"] = invitee_user_id
        stmt = (
            t.invites.update()
            .where(
                sa.and_(
                    t.invites.c.id == invite_id,
                    t.invites.c.state == InviteState.PENDING.value,
                )
            )
            .values(**values)
            .returning(t.invites)
        )
        row = (await self._db.execute(stmt)).first()
        return _row_to_invite(row) if row else None


def _row_to_invite(row: Any) -> Invite:
    return Invite(
        id=row.id,
        scope_type=InviteScope(row.scope_type),
        scope_id=row.scope_id,
        role=row.role,
        inviter_user_id=row.inviter_user_id,
        invitee_email=row.invitee_email,
        invitee_user_id=row.invitee_user_id,
        state=InviteState(row.state),
        token_hash=row.token_hash,
        expires_at=row.expires_at,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
    )


# ---------- OC transfer ---------------------------------------------------


class OCTransferRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        org_id: uuid.UUID,
        initiator_user_id: uuid.UUID,
        target_user_id: uuid.UUID,
        ttl: timedelta = timedelta(days=7),
    ) -> OCTransfer:
        try:
            row = (
                await self._db.execute(
                    t.original_creator_transfers.insert()
                    .values(
                        org_id=org_id,
                        initiator_user_id=initiator_user_id,
                        target_user_id=target_user_id,
                        expires_at=now() + ttl,
                    )
                    .returning(t.original_creator_transfers)
                )
            ).one()
        except IntegrityError as exc:
            raise TransferConflict("another transfer is pending for this org") from exc
        return _row_to_oc(row)

    async def get(self, transfer_id: uuid.UUID) -> OCTransfer | None:
        row = (
            await self._db.execute(
                t.original_creator_transfers.select().where(t.original_creator_transfers.c.id == transfer_id)
            )
        ).first()
        return _row_to_oc(row) if row else None

    async def list_pending(self, org_id: uuid.UUID) -> Sequence[OCTransfer]:
        rows = (
            await self._db.execute(
                t.original_creator_transfers.select().where(
                    sa.and_(
                        t.original_creator_transfers.c.org_id == org_id,
                        t.original_creator_transfers.c.resolved_at.is_(None),
                    )
                )
            )
        ).all()
        return [_row_to_oc(r) for r in rows]

    async def resolve(self, transfer_id: uuid.UUID, new_state: OCTransferState) -> OCTransfer | None:
        stmt = (
            t.original_creator_transfers.update()
            .where(
                sa.and_(
                    t.original_creator_transfers.c.id == transfer_id,
                    t.original_creator_transfers.c.resolved_at.is_(None),
                )
            )
            .values(state=new_state.value, resolved_at=now())
            .returning(t.original_creator_transfers)
        )
        row = (await self._db.execute(stmt)).first()
        return _row_to_oc(row) if row else None

    async def expire_due(self) -> int:
        """Set state='expired' on any pending transfer past its `expires_at`."""
        result = await self._db.execute(
            t.original_creator_transfers.update()
            .where(
                sa.and_(
                    t.original_creator_transfers.c.resolved_at.is_(None),
                    t.original_creator_transfers.c.expires_at < now(),
                )
            )
            .values(
                state=OCTransferState.EXPIRED.value,
                resolved_at=now(),
            )
        )
        return result.rowcount or 0


def _row_to_oc(row: Any) -> OCTransfer:
    return OCTransfer(
        id=row.id,
        org_id=row.org_id,
        initiator_user_id=row.initiator_user_id,
        target_user_id=row.target_user_id,
        state=OCTransferState(row.state),
        created_at=row.created_at,
        expires_at=row.expires_at,
        resolved_at=row.resolved_at,
    )


__all__ = [
    "InviteRepository",
    "OCTransferRepository",
    "OrgMemberRepository",
    "OrgRepository",
    "ProjectMemberRepository",
    "ProjectRepository",
    "_hash_token",
    "_new_token",
]
