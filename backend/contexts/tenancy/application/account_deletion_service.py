"""Account-deletion cascade for the tenancy context (R8.14 / R8.18).

Extracted from TenancyFacade so that business logic lives in the application
layer while the facade remains a thin delegation surface.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.tenancy.domain.errors import MemberNotFound, OriginalCreatorConflict
from contexts.tenancy.domain.models import Project
from contexts.tenancy.infrastructure import tables as t
from contexts.tenancy.infrastructure.repositories import (
    InviteRepository,
    OCTransferRepository,
    OrgMemberRepository,
    OrgRepository,
    ProjectMemberRepository,
    ProjectRepository,
)


class AccountDeletionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._orgs = OrgRepository(db)
        self._projects = ProjectRepository(db)
        self._org_members = OrgMemberRepository(db)
        self._project_members = ProjectMemberRepository(db)
        self._invites = InviteRepository(db)
        self._transfers = OCTransferRepository(db)

    async def orgs_blocking_self_delete(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        """R8.18: Orgs where the user is Original Creator AND >= 2 active members.

        Delegates to the canonical implementation on ``OrgService`` rather than
        re-deriving the rule, so the block list can never drift from it.
        """
        from contexts.tenancy.application.org_service import OrgService

        return await OrgService(self._db).orgs_blocking_self_delete(user_id)

    async def cascade_account_deletion(
        self,
        *,
        user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> dict[str, int]:
        """R8.14: tear down a self-deleting user's tenancy footprint.

        The R8.18 block check (:meth:`orgs_blocking_self_delete`) MUST have
        already returned empty -- this method assumes no Org the user is OC of
        has other active members. It:

        * soft-deletes every Individual-owned Project;
        * removes the user from every *other* project membership -- including the
          projects of a reaped solo Org -- so the keys they carried in are revoked
          (R7.04) and the membership row is dropped before the project is deleted;
        * for Orgs the user solely owns as OC, soft-deletes the Org (via
          ``OrgService.soft_delete`` so it emits ``org.deleted`` like every other
          org deletion) and its projects, so no Org outlives its only member
          (R8.14: "or the Org is deleted"); for all other Orgs, just drops the
          membership.

        Returns counts for the aggregate ``user.self_deleted`` audit record.
        """
        from contexts.tenancy.application.org_service import OrgService
        from contexts.tenancy.application.project_service import ProjectService

        proj_svc = ProjectService(self._db)
        org_svc = OrgService(self._db)

        owned = await self._projects.list_by_user(user_id)
        owned_ids = {p.id for p in owned}
        for project in owned:
            await proj_svc.soft_delete(
                project_id=project.id,
                actor_user_id=user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )

        # Classify orgs once, caching each solo-OC org's projects so the reap
        # loop below doesn't re-query list_by_org.
        solo_org_projects: dict[uuid.UUID, list[Project]] = {}
        surviving_orgs: list[uuid.UUID] = []
        for org in await self._orgs.list_for_user(user_id):
            oc = await self._org_members.original_creator(org.id)
            member_count = await self._org_members.count_active_members(org.id)
            if oc is not None and oc.user_id == user_id and member_count <= 1:
                solo_org_projects[org.id] = list(await self._projects.list_by_org(org.id))
            else:
                surviving_orgs.append(org.id)

        # Leave every OTHER project the user is a member of -- reaped solo-org
        # projects included -- so remove_member revokes their carried keys (R7.04)
        # before the reap loop soft-deletes the project.
        projects_left = 0
        for project_id in await self._project_members.list_project_ids_for_user(user_id):
            if project_id in owned_ids:
                continue
            await proj_svc.remove_member(
                project_id=project_id,
                target_user_id=user_id,
                actor_user_id=user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )
            projects_left += 1

        invites_revoked = await self._invites.revoke_pending_by_inviter(user_id)
        transfers_cancelled = await self._transfers.cancel_pending_for_user(user_id)

        for org_id, projects in solo_org_projects.items():
            for project in projects:
                await proj_svc.soft_delete(
                    project_id=project.id,
                    actor_user_id=user_id,
                    actor_ip=actor_ip,
                    request_id=request_id,
                )
            await org_svc.soft_delete(
                org_id=org_id,
                actor_user_id=user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )

        for org_id in surviving_orgs:
            try:
                await org_svc.remove_member(
                    org_id=org_id,
                    target_user_id=user_id,
                    actor_user_id=user_id,
                    actor_ip=actor_ip,
                    request_id=request_id,
                )
            except (MemberNotFound, OriginalCreatorConflict):
                await self._org_members.remove(org_id=org_id, user_id=user_id)

        return {
            "projects_deleted": len(owned),
            "projects_left": projects_left,
            "orgs_left": len(surviving_orgs),
            "solo_orgs_deleted": len(solo_org_projects),
            "invites_revoked": invites_revoked,
            "transfers_cancelled": transfers_cancelled,
        }

    async def prepare_hard_delete(
        self,
        *,
        user_id: uuid.UUID,
        reassign_to_user_id: uuid.UUID,
    ) -> None:
        """Remove all FK RESTRICT references to the user before hard-delete.

        Soft-deleted orgs/projects are hard-deleted outright (GDPR purge);
        their FK CASCADE constraints clean up child rows automatically.
        Active rows that still reference the user (edge case if cascade was
        incomplete) are reassigned to *reassign_to_user_id*.
        """
        # F-24 (C1): this is the immediate admin GDPR hard-delete — it bypasses
        # the retention worker, so wire the same project-scoped source-infra
        # teardown here or admin purges would leak File RAG / Knowledge Map source
        # blobs and the rag_{project_id} collection until the next backstop sweep.
        # Enumerate the projects this call will erase — those deleted directly
        # (below) plus those cascade-deleted via the orgs it deletes — BEFORE the
        # rows (and their blob-key-carrying rag_* rows) cascade away, then erase
        # each project's source infra keyed on project_id alone.
        deleted_projects_cond = sa.and_(
            sa.or_(
                t.projects.c.owner_user_id == user_id,
                t.projects.c.created_by_user_id == user_id,
            ),
            t.projects.c.deleted_at.isnot(None),
        )
        deleted_orgs = sa.select(t.orgs.c.id).where(
            sa.and_(
                t.orgs.c.creator_user_id == user_id,
                t.orgs.c.deleted_at.isnot(None),
            )
        )
        teardown_ids = set(
            (await self._db.execute(sa.select(t.projects.c.id).where(deleted_projects_cond))).scalars().all()
        ) | set(
            (
                await self._db.execute(
                    sa.select(t.projects.c.id).where(t.projects.c.owner_org_id.in_(deleted_orgs))
                )
            )
            .scalars()
            .all()
        )
        if teardown_ids:
            from loguru import logger

            from contexts.knowledge.interfaces.facade import KnowledgeFacade

            facade = KnowledgeFacade(self._db)
            for pid in teardown_ids:
                try:
                    await facade.purge_project_source_infra(pid)
                except Exception:
                    # Best-effort (spec §7.2): a teardown failure must never abort
                    # the GDPR purge — the backstop sweep reclaims the orphan.
                    logger.bind(event="admin_gdpr_rag_source_teardown_failed", project_id=str(pid)).opt(
                        exception=True
                    ).warning("rag source teardown failed for project during admin hard-delete")

        await self._db.execute(
            t.original_creator_transfers.delete().where(
                sa.or_(
                    t.original_creator_transfers.c.initiator_user_id == user_id,
                    t.original_creator_transfers.c.target_user_id == user_id,
                )
            )
        )
        await self._db.execute(
            t.projects.delete().where(
                sa.and_(
                    sa.or_(
                        t.projects.c.owner_user_id == user_id,
                        t.projects.c.created_by_user_id == user_id,
                    ),
                    t.projects.c.deleted_at.isnot(None),
                )
            )
        )
        await self._db.execute(
            t.projects.update().where(t.projects.c.owner_user_id == user_id).values(owner_user_id=None)
        )
        await self._db.execute(
            t.projects.update()
            .where(t.projects.c.created_by_user_id == user_id)
            .values(created_by_user_id=reassign_to_user_id)
        )
        await self._db.execute(
            t.orgs.delete().where(
                sa.and_(
                    t.orgs.c.creator_user_id == user_id,
                    t.orgs.c.deleted_at.isnot(None),
                )
            )
        )
        await self._db.execute(
            t.orgs.update()
            .where(t.orgs.c.creator_user_id == user_id)
            .values(creator_user_id=reassign_to_user_id)
        )


__all__ = ["AccountDeletionService"]
