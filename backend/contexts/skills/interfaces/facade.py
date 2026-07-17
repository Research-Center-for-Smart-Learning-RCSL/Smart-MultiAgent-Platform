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

from app.config.settings import get_settings
from contexts.agents.domain.models import AgentToolType
from contexts.skills.application.binding_service import BindingService, BoundSet, DroppedSkill
from contexts.skills.application.file_service import (
    MAX_SKILL_FILE_BYTES,
    SkillFileService,
    enqueue_skill_scan,
)
from contexts.skills.application.index_builder import render_index
from contexts.skills.application.skill_service import SkillService
from contexts.skills.domain.models import (
    Skill,
    SkillDraft,
    SkillFile,
    SkillScope,
    SkillScopeCounts,
    SkillSource,
)
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

    # -- files (the scope routers) -------------------------------------------

    async def list_files(
        self, skill_id: uuid.UUID, scope: SkillScope, *, owner_id: uuid.UUID | None
    ) -> list[SkillFile]:
        skill = await SkillService(self._db).get_owned(skill_id, scope, owner_id=owner_id)
        return await SkillFileService(self._db).list_for_skill(skill.id)

    async def add_file(
        self,
        skill_id: uuid.UUID,
        scope: SkillScope,
        *,
        owner_id: uuid.UUID | None,
        path: str,
        data: bytes,
        mime: str,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> SkillFile:
        """Add a file to a skill the caller owns, then queue its scan.

        Ownership first, always: `get_owned` raises `SkillNotFound` (404, never 403) for
        a skill under a scope the caller does not hold, so no byte is stored and no row
        written for a skill they cannot reach.

        **The commit before the enqueue is load-bearing, not tidiness.** `skill_scan_file`
        looks the row up on its own connection, so an enqueue inside this request's open
        transaction is a race the worker can win — and losing it is terminal, because the
        worker's "not found" arm returns rather than retries. The file would sit `pending`
        forever, which under AC-34's fail-closed gate means the skill never becomes
        readable. `db_session`'s docstring sanctions the explicit commit for exactly this
        ("enqueueing Arq jobs that reference a just-written row"), and the RAG path does
        the same at `ingest_service.py:234`.
        """
        skill = await SkillService(self._db).get_owned(skill_id, scope, owner_id=owner_id)
        created = await SkillFileService(self._db).add(
            skill=skill,
            path=path,
            data=data,
            mime=mime,
            scan_enabled=get_settings().security.file_scan_enabled,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )
        await self._db.commit()
        await enqueue_skill_scan(file_id=created.id, sha256=created.sha256)
        return created

    async def update_file(
        self,
        skill_id: uuid.UUID,
        scope: SkillScope,
        file_id: uuid.UUID,
        *,
        owner_id: uuid.UUID | None,
        data: bytes,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> SkillFile:
        skill = await SkillService(self._db).get_owned(skill_id, scope, owner_id=owner_id)
        files = SkillFileService(self._db)
        current = await files.get_owned(skill.id, file_id)
        updated = await files.update_content(
            skill=skill,
            file=current,
            data=data,
            scan_enabled=get_settings().security.file_scan_enabled,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )
        # Committed before the enqueue for the reason `add_file` documents. The exposure
        # is the same here and slightly worse in effect: an edit resets `scan_status` to
        # `pending`, so a lost scan takes a *working* skill dark rather than merely
        # delaying a new one.
        await self._db.commit()
        await enqueue_skill_scan(file_id=updated.id, sha256=updated.sha256)
        return updated

    async def delete_file(
        self,
        skill_id: uuid.UUID,
        scope: SkillScope,
        file_id: uuid.UUID,
        *,
        owner_id: uuid.UUID | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        skill = await SkillService(self._db).get_owned(skill_id, scope, owner_id=owner_id)
        files = SkillFileService(self._db)
        current = await files.get_owned(skill.id, file_id)
        await files.delete(
            skill=skill,
            file=current,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    # -- cross-context ------------------------------------------------------

    async def resolve_bound_set(
        self,
        *,
        agent_id: uuid.UUID,
        agent_project_id: uuid.UUID,
        enabled_tools: set[AgentToolType],
    ) -> BoundSet:
        """One turn's validated snapshot. The single entry point for both turn paths.

        `enabled_tools` is the caller's, like `agent_project_id`: the turn reads the agent's
        tools once and every consumer shares that one snapshot, so a tool toggled mid-turn
        cannot make the runtime and this tap disagree about the same agent.
        """
        return await BindingService(self._db).resolve_bound_set(
            agent_id=agent_id, agent_project_id=agent_project_id, enabled_tools=enabled_tools
        )

    async def read_skill_file_text(self, file: SkillFile) -> str:
        """The extracted text of one reference file from a turn's snapshot (AC-18).

        Takes the `SkillFile` rather than an id, and that is the access control: the only
        way to obtain one is from `resolve_bound_set`, so the tap has already proven this
        agent may see it. An id-taking method would be a read primitive over every
        tenant's files — the same reason `read_skill` never resolves a *name* against the
        table (§6).

        Exposed here rather than letting the runtime import `file_service`, per
        backend/CLAUDE.md: `contexts/agents` reaches this context through its facade only.
        """
        return await SkillFileService(self._db).read_text(file)

    async def read_skill_file_bytes(self, file: SkillFile) -> bytes:
        """One script's raw bytes, for staging into the sandbox ([R31.22] / AC-21).

        Same access-control shape as `read_skill_file_text`: it takes the row, which only
        `resolve_bound_set` hands out, so the tap has already proven the agent may have it.
        Unlike that method these bytes never enter the model's context — they are tarred
        into the agent's volume and interpreted, if at all, inside gVisor with no network.
        """
        return await SkillFileService(self._db).read_bytes(file)

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


# `BoundSet` and `DroppedSkill` are re-exported deliberately: the first is
# `resolve_bound_set`'s return type and the second is what the runtime must construct to
# hand a staging failure back to `BoundSet.without`, so the agents runtime has to name
# both — and it must not have to reach past this module into `application/` to do so.
# MAX_SKILL_FILE_BYTES is re-exported so the routers can take the upload cap from this
# context's public surface rather than reaching into `application/` for it — which this
# module's own docstring says they never do. It is the same constant, named where the
# rule says to name it.
__all__ = ["MAX_SKILL_FILE_BYTES", "BoundSet", "DroppedSkill", "SkillsFacade"]
