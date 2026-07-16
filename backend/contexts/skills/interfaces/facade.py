"""skills facade — the cross-context read surface (§31).

What other contexts need from skills is narrow and deliberately so:

- the **agents runtime** resolves a turn's bound set and reads one body by name from that
  snapshot (never by a query, which would be an unscoped read over the whole table);
- **agents / tenancy** cascade their own soft-deletes into skills, inside their own
  transaction, because a FK never fires on an UPDATE;
- **admin** counts skills per scope for [R31.11].

Nothing here exposes a write path into another tenant's rows: every method is either
keyed on an owner the caller already proved, or is the containment predicate itself.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.skills.application.binding_service import BindingService, BoundSet
from contexts.skills.application.skill_service import SkillService
from contexts.skills.domain.models import SkillScope, SkillScopeCounts
from contexts.skills.infrastructure.repositories import SkillRepository


class SkillsFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def resolve_bound_set(self, *, agent_id: uuid.UUID, agent_project_id: uuid.UUID) -> BoundSet:
        """One turn's validated snapshot. The single entry point for both turn paths."""
        return await BindingService(self._db).resolve_bound_set(
            agent_id=agent_id, agent_project_id=agent_project_id
        )

    async def cascade_agent_deleted(self, agent_id: uuid.UUID) -> list[uuid.UUID]:
        """Unbind a soft-deleted agent's skills and delete its agent-scoped ones (AC-38).

        Call inside the agent's own soft-delete transaction.
        """
        return await SkillService(self._db).cascade_agent_deleted(agent_id)

    async def cascade_owner_deleted(self, *, scope: SkillScope, owner_id: uuid.UUID) -> list[uuid.UUID]:
        """Soft-delete a project's or org's skills, inside that owner's transaction."""
        return await SkillService(self._db).cascade_owner_deleted(scope=scope, owner_id=owner_id)

    async def count_by_scope(self) -> SkillScopeCounts:
        """[R31.11] — live skills per scope, for the admin metric."""
        return await SkillRepository(self._db).count_by_scope()


__all__ = ["SkillsFacade"]
