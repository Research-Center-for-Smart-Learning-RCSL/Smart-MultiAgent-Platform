"""skills facade — the context's public surface (§31).

Everything outside `contexts/skills/` goes through here: the API routers and the other
contexts alike. backend/CLAUDE.md is explicit — "`app/api/v1/` calls only
`contexts/*/interfaces/facade.py` — never reach into application/ or infrastructure/",
and "Route handlers must ... call the context facade — never instantiate services
directly".

Two groups of callers:

- **the routers** — CRUD, bind/unbind, and the per-scope listing behind each scope path;
- **other contexts** — the agents runtime resolves a turn's bound set and reads one body
  from that snapshot (never by a query, which would be an unscoped read over the whole
  table); agents and tenancy cascade their own soft-deletes in their own transaction,
  because a FK never fires on an UPDATE; admin counts skills per scope for [R31.11].

Nothing here decides AuthZ. The router proves the caller may manage skills *in a scope*;
`_assert_owned` proves the skill *is* in it (404, never 403) and `resolve_bindable` proves
a skill's scope contains an agent. This layer only stops those controls from being
reachable around.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.skills.application.binding_service import BindingService, BoundSet
from contexts.skills.application.index_builder import render_index
from contexts.skills.application.skill_service import SkillService
from contexts.skills.domain.models import Skill, SkillDraft, SkillScope, SkillScopeCounts, SkillSource
from contexts.skills.infrastructure.repositories import SkillRepository


class SkillsFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # -- CRUD (the scope routers) -------------------------------------------

    async def list_for_scope(
        self,
        *,
        scope: SkillScope,
        owner_id: uuid.UUID | None,
        limit: int,
        offset: int,
        include_deleted: bool = False,
    ) -> tuple[list[Skill], int]:
        return await SkillService(self._db).list_for_scope(
            scope=scope, owner_id=owner_id, limit=limit, offset=offset, include_deleted=include_deleted
        )

    async def get_owned(
        self,
        skill_id: uuid.UUID,
        scope: SkillScope,
        *,
        owner_id: uuid.UUID | None,
        include_deleted: bool = False,
    ) -> Skill:
        return await SkillService(self._db).get_owned(
            skill_id, scope, owner_id=owner_id, include_deleted=include_deleted
        )

    async def create(
        self,
        *,
        scope: SkillScope,
        owner_id: uuid.UUID | None,
        name: str,
        description: str,
        body: str,
        requires: tuple[str, ...] = (),
        allowed_tools: tuple[str, ...] = (),
        extra_frontmatter: dict[str, Any] | None = None,
        source: SkillSource = SkillSource.AUTHORED,
        bundle_sha256: str | None = None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> Skill:
        return await SkillService(self._db).create(
            scope=scope,
            owner_id=owner_id,
            name=name,
            description=description,
            body=body,
            requires=requires,
            allowed_tools=allowed_tools,
            extra_frontmatter=extra_frontmatter,
            source=source,
            bundle_sha256=bundle_sha256,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def update(
        self,
        skill_id: uuid.UUID,
        scope: SkillScope,
        *,
        owner_id: uuid.UUID | None,
        draft: SkillDraft,
        expected_version: int | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> Skill:
        return await SkillService(self._db).update(
            skill_id,
            scope,
            owner_id=owner_id,
            draft=draft,
            expected_version=expected_version,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def soft_delete(
        self,
        skill_id: uuid.UUID,
        scope: SkillScope,
        *,
        owner_id: uuid.UUID | None,
        expected_version: int | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> list[uuid.UUID]:
        return await SkillService(self._db).soft_delete(
            skill_id,
            scope,
            owner_id=owner_id,
            expected_version=expected_version,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def restore(
        self,
        skill_id: uuid.UUID,
        scope: SkillScope,
        *,
        owner_id: uuid.UUID | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> Skill:
        return await SkillService(self._db).restore(
            skill_id,
            scope,
            owner_id=owner_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def copy(
        self,
        skill_id: uuid.UUID,
        scope: SkillScope,
        *,
        owner_id: uuid.UUID | None,
        target_scope: SkillScope,
        target_owner_id: uuid.UUID | None,
        name: str,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> Skill:
        return await SkillService(self._db).copy(
            skill_id,
            scope,
            owner_id=owner_id,
            target_scope=target_scope,
            target_owner_id=target_owner_id,
            name=name,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    # -- bindings (the binding router) --------------------------------------

    async def bind(self, *, skill_id: uuid.UUID, agent_id: uuid.UUID) -> Skill:
        """Bind after proving containment, requirements, name freedom, and budget."""
        return await BindingService(self._db).bind(skill_id=skill_id, agent_id=agent_id)

    async def unbind(self, *, skill_id: uuid.UUID, agent_id: uuid.UUID) -> Skill | None:
        """Unbind, returning what was unbound — or None when nothing was bound."""
        return await BindingService(self._db).unbind(skill_id=skill_id, agent_id=agent_id)

    # -- cross-context ------------------------------------------------------

    async def resolve_bound_set(self, *, agent_id: uuid.UUID, agent_project_id: uuid.UUID) -> BoundSet:
        """One turn's validated snapshot. The single entry point for both turn paths."""
        return await BindingService(self._db).resolve_bound_set(
            agent_id=agent_id, agent_project_id=agent_project_id
        )

    @staticmethod
    def render_index(skills: Sequence[Skill]) -> str:
        """The skills index block for a turn's system prompt ([R31.12]-[R31.14]).

        Exposed here rather than letting the runtime import `index_builder` directly: the
        frame the block is wrapped in and the charset rule that keeps a description from
        forging it are one control, and it lives inside this context. Static because it is
        pure — no session, no query — and the runtime calls it on the snapshot it already
        holds.
        """
        return render_index(skills)

    async def ensure_index_cap_fits(self, agent_id: uuid.UUID, cap: int | None) -> None:
        """Refuse a `skill_index_token_cap` the agent's current index already exceeds.

        Owned here rather than by the agents context because the arithmetic is the index's:
        agents holds the column, skills knows what is bound and what it renders to. Raises
        `SkillIndexBudgetExceeded`, which the shared RFC 7807 handler maps wherever it
        surfaces — including from the agents router.

        `ensure_`, not the `assert_` the internals use: `unittest.mock` refuses to fake any
        attribute whose name starts with "assert" (it guards against typo'd assertions), so
        an `assert_`-prefixed method on a cross-context facade cannot be mocked by the
        callers who must mock it.
        """
        await BindingService(self._db).assert_cap_fits_current_index(agent_id, cap)

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


# `BoundSet` is re-exported deliberately: it is `resolve_bound_set`'s return type, so the
# agents runtime has to name it, and it must not have to reach past this module into
# `application/` to do so.
__all__ = ["BoundSet", "SkillsFacade"]
