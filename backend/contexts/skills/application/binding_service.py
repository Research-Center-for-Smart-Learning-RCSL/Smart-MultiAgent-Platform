"""The containment predicate and the per-turn snapshot (§31, [R31.07]-[R31.09]).

`resolve_bindable` is the single place §5's matrix is evaluated. It is the control for
**both** the bind endpoint and the turn-time tap; nothing else may decide whether a skill
may reach an agent.

Why it is a distinct control from `skill_service._assert_owned`: the bind endpoint has no
scope in its path, so `_assert_owned` cannot be its guard. Accepting a `skill_id`
unchecked at bind is the SEC-H1 shape verbatim — the IDOR where a member of Project A
attached Project B's config and exfiltrated its chunks — except that here it leaks
*instructions into another tenant's agent*.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.domain.models import AgentToolType
from contexts.agents.interfaces.facade import AgentsFacade
from contexts.skills.application._scoping import ProjectOwnership, resolve_project_scope
from contexts.skills.application.index_builder import estimate_index_tokens
from contexts.skills.domain.errors import (
    SkillContainmentFailed,
    SkillIndexBudgetExceeded,
    SkillNameTaken,
    SkillNotFound,
    SkillRequiresToolMissing,
)
from contexts.skills.domain.models import Skill, SkillScope
from contexts.skills.infrastructure.repositories import (
    SkillBindingRepository,
    SkillRepository,
)
from contexts.tenancy.interfaces.facade import TenancyFacade

# The `requires:` vocabulary is a closed set of built-in tool names (OQ-3, closed by
# Q-31). `update_wakeup` is excluded deliberately: it is added unconditionally by
# build_registry, so `requires: [update_wakeup]` is trivially satisfiable and would
# masquerade as a real constraint. MCP ids are inexpressible — `mcp__{id}__{name}`
# embeds a per-agent server id, so a portable skill cannot name one.
REQUIRES_VOCABULARY: dict[str, AgentToolType] = {
    "code_exec": AgentToolType.HOSTED_CODE_INTERPRETER,
    "web_search": AgentToolType.HOSTED_WEB_SEARCH,
    "file": AgentToolType.HOSTED_FILE_WORKSPACE,
    "file_search": AgentToolType.HOSTED_FILE_SEARCH,
}

# Q-13. `agents.skill_index_token_cap` overrides it per agent; NULL means this.
DEFAULT_SKILL_INDEX_TOKEN_CAP = 3000


@dataclass(frozen=True, slots=True)
class DroppedSkill:
    """A skill removed from a turn's snapshot, with the reason for the audit event."""

    skill_id: uuid.UUID
    name: str
    reason: str


@dataclass(frozen=True, slots=True)
class BoundSet:
    """One turn's validated snapshot plus whatever fell out of it.

    `dropped` is non-empty when a binding went stale between trigger and execution. The
    turn still runs: copying the key-group tap's turn-skip would make revocation an
    availability attack, since an agent cannot run without a key but runs perfectly well
    without one of twenty skills ([R31.08]).
    """

    skills: tuple[Skill, ...]
    dropped: tuple[DroppedSkill, ...] = ()


class BindingService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._skills = SkillRepository(db)
        self._bindings = SkillBindingRepository(db)
        self._agents = AgentsFacade(db)
        self._tenancy = TenancyFacade(db)

    # -- the predicate -------------------------------------------------------

    async def resolve_bindable(self, skill_id: uuid.UUID, agent_id: uuid.UUID) -> Skill:
        """Prove the skill's scope contains the agent, or raise.

        Liveness is checked **first and explicitly**, because it is a precondition rather
        than a property of the matrix: three of the four rows are column comparisons that
        cannot see a soft delete, and `platform` is the constant True. Only the `org` row
        would fail closed on its own, and only by accident.
        """
        skill = await self._skills.get(skill_id)
        if skill is None:
            raise SkillNotFound(skill_id)

        agent = await self._agents.get_agent(agent_id)
        if agent is None:
            raise SkillContainmentFailed("agent_gone")

        await self._assert_contains(skill, agent_id=agent.id, agent_project_id=agent.project_id)
        return skill

    async def _assert_contains(
        self,
        skill: Skill,
        *,
        agent_id: uuid.UUID,
        agent_project_id: uuid.UUID,
    ) -> None:
        """§5's matrix, total over all four scopes."""
        if skill.scope is SkillScope.PLATFORM:
            return

        if skill.scope is SkillScope.AGENT:
            if skill.agent_id != agent_id:
                raise SkillContainmentFailed("agent_scope_mismatch")
            return

        if skill.scope is SkillScope.PROJECT:
            if skill.project_id != agent_project_id:
                raise SkillContainmentFailed("project_scope_mismatch")
            return

        # org: the agent's project must be owned by *that* org, and both sides must be
        # non-NULL. An org skill must never bind into an individually-owned project,
        # whose owner_org_id is NULL — comparing NULL to NULL would otherwise read as a
        # match in any formulation that treats "no org" as a value.
        project = await resolve_project_scope(self._tenancy, agent_project_id)
        if project.ownership is ProjectOwnership.NONEXISTENT:
            raise SkillContainmentFailed("project_gone")
        if project.ownership is ProjectOwnership.DELETED:
            raise SkillContainmentFailed("project_deleted")
        if project.ownership is ProjectOwnership.INDIVIDUAL:
            raise SkillContainmentFailed("project_individually_owned")
        if skill.org_id is None or project.owner_org_id != skill.org_id:
            raise SkillContainmentFailed("org_scope_mismatch")

    # -- requirements (Q-9 / [R31.09]) ---------------------------------------

    async def assert_requirements(self, skill: Skill, agent_id: uuid.UUID) -> None:
        """Re-check `requires:` against the agent's enabled tools.

        Checked at bind *and* every turn. The reviewed design condemned bind-time-only
        authorization and then made `requires:` exactly that: disabling code_exec after
        a bind would leave the model reading a SKILL.md whose script cannot run, and
        confabulating (AC-35).
        """
        needed = self._required_tool_types(skill)
        if not needed:
            return
        enabled = {t.tool_type for t in await self._agents.list_agent_tools(agent_id) if t.enabled}
        for name, tool_type in needed.items():
            if tool_type not in enabled:
                raise SkillRequiresToolMissing(name, skill_name=skill.name)

    @staticmethod
    def _required_tool_types(skill: Skill) -> dict[str, AgentToolType]:
        """Map the declaration to tool types, ignoring names outside the vocabulary.

        Unknown names are a compatibility warning at import, not a hard failure here
        (§8 threat 6) — but they are also never silently *satisfiable*: an unknown name
        maps to no tool, so it cannot be used to claim a requirement is met.
        """
        return {n: REQUIRES_VOCABULARY[n] for n in skill.requires if n in REQUIRES_VOCABULARY}

    # -- bind / unbind -------------------------------------------------------

    async def assert_name_free_in_bound_set(
        self,
        agent_id: uuid.UUID,
        name: str,
        *,
        excluding_skill_id: uuid.UUID | None = None,
    ) -> None:
        """Q-30: names are unique across an agent's *bound set*, not just per scope.

        [R31.03]'s per-scope-holder uniqueness cannot see across scopes, so two legal
        skills — one project, one org — can both be named `pdf-fill`. Fixed precedence
        would be an attack: a project member creates a same-named skill and silently
        shadows the admin's platform one, defeating Q-7's opt-in, which is the only
        control over platform ambient authority. `/workspace/skills/{name}/` collides
        identically.
        """
        for bound in await self._bindings.list_live_for_agent(agent_id):
            if bound.name == name and bound.id != excluding_skill_id:
                raise SkillNameTaken(name, agent_ids=(agent_id,))

    async def agents_conflicting_on_name(self, skill: Skill) -> tuple[uuid.UUID, ...]:
        """Agents whose bound set already holds `skill.name` via a *different* skill.

        Used by restore (AC-39): while a skill was soft-deleted, another one may have
        taken its name inside an agent's bound set, so re-attaching its cascaded
        bindings would reintroduce exactly the ambiguity Q-30 forbids.
        """
        conflicts: list[uuid.UUID] = []
        for agent_id in await self._bindings.list_agent_ids_cascade_unbound_from(skill.id):
            for bound in await self._bindings.list_live_for_agent(agent_id):
                if bound.name == skill.name and bound.id != skill.id:
                    conflicts.append(agent_id)
                    break
        return tuple(conflicts)

    # -- index budget (Q-13 / [R31.13]-[R31.14]) -----------------------------

    async def index_cap_for(self, agent_id: uuid.UUID) -> int:
        agent = await self._agents.get_agent(agent_id)
        cap = agent.skill_index_token_cap if agent else None
        return cap if cap is not None else DEFAULT_SKILL_INDEX_TOKEN_CAP

    async def assert_index_fits(
        self,
        agent_id: uuid.UUID,
        *,
        adding: Skill | None = None,
        replacing: Skill | None = None,
        cap_override: int | None = None,
    ) -> None:
        """Reject at bind, at description update, and at cap lowering.

        Never truncated at turn time: showing the model half an index is worse than
        refusing the change, because the model cannot tell a short menu from a
        complete one ([R31.14]).
        """
        bound = list(await self._bindings.list_live_for_agent(agent_id))
        if replacing is not None:
            bound = [replacing if s.id == replacing.id else s for s in bound]
        if adding is not None and not any(s.id == adding.id for s in bound):
            bound.append(adding)

        cap = cap_override if cap_override is not None else await self.index_cap_for(agent_id)
        required = estimate_index_tokens(bound)
        if required > cap:
            raise SkillIndexBudgetExceeded(required=required, cap=cap, agent_ids=(agent_id,))

    async def agents_over_index_cap(
        self, skill: Skill, *, cap_override: int | None = None
    ) -> tuple[uuid.UUID, ...]:
        """Agents whose index would overflow if `skill` took its new description.

        Drives the update path's rejection: lengthening a description is a write to the
        *skill*, but the budget it can break belongs to every *agent* bound to it, so
        the check fans out and the error names them.
        """
        over: list[uuid.UUID] = []
        for agent_id in await self._bindings.list_agent_ids_bound_to(skill.id):
            try:
                await self.assert_index_fits(agent_id, replacing=skill, cap_override=cap_override)
            except SkillIndexBudgetExceeded:
                over.append(agent_id)
        return tuple(over)

    async def bind(self, *, skill_id: uuid.UUID, agent_id: uuid.UUID) -> Skill:
        """Bind after proving containment, requirements, name freedom, and budget."""
        skill = await self.resolve_bindable(skill_id, agent_id)
        await self.assert_requirements(skill, agent_id)
        await self.assert_name_free_in_bound_set(agent_id, skill.name, excluding_skill_id=skill.id)
        await self.assert_index_fits(agent_id, adding=skill)
        await self._bindings.bind(agent_id=agent_id, skill_id=skill_id)
        return skill

    async def unbind(self, *, skill_id: uuid.UUID, agent_id: uuid.UUID) -> bool:
        return await self._bindings.unbind(agent_id=agent_id, skill_id=skill_id)

    # -- the per-turn snapshot ([R31.08]) ------------------------------------

    async def resolve_bound_set(self, *, agent_id: uuid.UUID, agent_project_id: uuid.UUID) -> BoundSet:
        """Re-prove every binding at the start of a turn, on both turn paths.

        Bind-time-only authorization is the live anti-pattern this codebase already has
        (`prompt_studio`'s session `post_message`, §8.5); Skills follows turn_engine and
        re-validates each turn instead. Failure is **per skill**: a stale binding drops
        that skill and the turn proceeds.
        """
        kept: list[Skill] = []
        dropped: list[DroppedSkill] = []

        for skill in await self._bindings.list_live_for_agent(agent_id):
            try:
                await self._assert_contains(skill, agent_id=agent_id, agent_project_id=agent_project_id)
                await self.assert_requirements(skill, agent_id)
            except SkillContainmentFailed as exc:
                dropped.append(DroppedSkill(skill_id=skill.id, name=skill.name, reason=exc.reason))
                continue
            except SkillRequiresToolMissing as exc:
                dropped.append(
                    DroppedSkill(skill_id=skill.id, name=skill.name, reason=f"requires:{exc.tool}")
                )
                continue
            kept.append(skill)

        return BoundSet(skills=tuple(kept), dropped=tuple(dropped))


__all__ = ["REQUIRES_VOCABULARY", "BindingService", "BoundSet", "DroppedSkill"]
