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
    ) -> set[uuid.UUID]:
        """Remove all FK RESTRICT references to the user before hard-delete.

        Soft-deleted orgs/projects are hard-deleted outright (GDPR purge);
        their FK CASCADE constraints clean up child rows automatically.
        Active rows that still reference the user (edge case if cascade was
        incomplete) are reassigned to *reassign_to_user_id*.

        Returns the ids of the projects this call erased. The caller must commit
        before passing them to ``purge_hard_deleted_project_sources`` — this
        method performs no external work of its own (F-7).
        """
        # F-24 (C1): this is the immediate admin GDPR hard-delete — it bypasses
        # the retention worker, so its projects need the same source-infra
        # teardown or admin purges leak File RAG / Knowledge Map source blobs and
        # the rag_{project_id} collection until the next backstop sweep.
        #
        # F-7: which projects die is decided by deleting them with RETURNING, not
        # by reading candidates first. A restore racing that read would otherwise
        # leave a live project stripped of sources it can never rebuild.
        doomed_orgs = sa.select(t.orgs.c.id).where(
            sa.and_(
                t.orgs.c.creator_user_id == user_id,
                t.orgs.c.deleted_at.isnot(None),
            )
        )
        # Captured before the org delete: an org-owned project vanishes by FK
        # cascade, leaving no row to attribute to the returned org id.
        org_projects: dict[uuid.UUID, set[uuid.UUID]] = {}
        rows = await self._db.execute(
            sa.select(t.projects.c.id, t.projects.c.owner_org_id).where(
                t.projects.c.owner_org_id.in_(doomed_orgs)
            )
        )
        for pid, oid in rows.all():
            org_projects.setdefault(oid, set()).add(pid)

        await self._db.execute(
            t.original_creator_transfers.delete().where(
                sa.or_(
                    t.original_creator_transfers.c.initiator_user_id == user_id,
                    t.original_creator_transfers.c.target_user_id == user_id,
                )
            )
        )
        deleted_projects = (
            (
                await self._db.execute(
                    t.projects.delete()
                    .where(
                        sa.and_(
                            sa.or_(
                                t.projects.c.owner_user_id == user_id,
                                t.projects.c.created_by_user_id == user_id,
                            ),
                            t.projects.c.deleted_at.isnot(None),
                        )
                    )
                    .returning(t.projects.c.id)
                )
            )
            .scalars()
            .all()
        )
        committed: set[uuid.UUID] = set(deleted_projects)

        await self._db.execute(
            t.projects.update().where(t.projects.c.owner_user_id == user_id).values(owner_user_id=None)
        )
        await self._db.execute(
            t.projects.update()
            .where(t.projects.c.created_by_user_id == user_id)
            .values(created_by_user_id=reassign_to_user_id)
        )
        deleted_orgs = (
            (
                await self._db.execute(
                    t.orgs.delete()
                    .where(
                        sa.and_(
                            t.orgs.c.creator_user_id == user_id,
                            t.orgs.c.deleted_at.isnot(None),
                        )
                    )
                    .returning(t.orgs.c.id)
                )
            )
            .scalars()
            .all()
        )
        for oid in deleted_orgs:
            committed |= org_projects.get(oid, set())

        await self._db.execute(
            t.orgs.update()
            .where(t.orgs.c.creator_user_id == user_id)
            .values(creator_user_id=reassign_to_user_id)
        )
        return committed

    async def purge_hard_deleted_project_sources(self, project_ids: set[uuid.UUID]) -> int:
        """Erase source infra for projects whose hard delete already committed (F-7).

        Best-effort by design: the committed row absence is the durable retry
        signal, so anything failing here stays discoverable by the retention
        orphan sweep. A teardown failure must never fail an already-committed
        GDPR purge. Returns the count purged without error.
        """
        if not project_ids:
            return 0

        from loguru import logger

        from contexts.knowledge.interfaces.facade import KnowledgeFacade

        try:
            purged: int = await KnowledgeFacade(self._db).purge_project_source_infra_batch(project_ids)
            await self._db.commit()
        except Exception:
            # Discard the partial audit writes: the hard delete is already
            # committed, and leaving a failed transaction on the session would
            # make `db_session`'s trailing commit raise and turn a successful
            # GDPR purge into a 500.
            await self._db.rollback()
            # The batch isolates per-project failures internally; this only
            # covers a catastrophic one (e.g. Qdrant client construction).
            logger.bind(
                event="admin_gdpr_rag_source_teardown_failed",
                projects_committed=len(project_ids),
            ).opt(exception=True).warning(
                "rag source teardown batch failed during admin hard-delete; orphan sweep will reclaim"
            )
            return 0
        if purged < len(project_ids):
            logger.bind(
                event="admin_gdpr_rag_source_teardown_partial",
                projects_committed=len(project_ids),
                projects_purged=purged,
            ).warning("rag source teardown partially failed; orphan sweep will reclaim the remainder")
        return purged


__all__ = ["AccountDeletionService"]
