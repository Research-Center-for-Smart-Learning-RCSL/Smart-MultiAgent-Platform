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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

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
    SkillScopeMismatch,
)
from contexts.skills.domain.models import Skill, SkillFile, SkillScope
from contexts.skills.infrastructure.repositories import (
    SkillBindingRepository,
    SkillFileRepository,
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

# The requirement a `scripts/` file implies on its own (AC-20). It is `code_exec` and not
# some new name because [R31.22] stages scripts for exactly that tool, and
# `_stage_workspace_inputs` gates staging on exactly that tool — the derived requirement
# has to name the gate it is protecting against, or it protects against nothing.
DERIVED_SCRIPT_REQUIREMENT = "code_exec"

# Q-13. `agents.skill_index_token_cap` overrides it per agent; NULL means this.
DEFAULT_SKILL_INDEX_TOKEN_CAP = 3000


@dataclass(frozen=True, slots=True)
class IndexBreach:
    """One agent's index overflow: what it would need against what it allows.

    Both numbers travel together because they are only meaningful as a pair. Each bound
    agent has its own cap and its own bound set, so "required" from one agent beside
    "cap" from another describes a state no agent is in.
    """

    agent_id: uuid.UUID
    required: int
    cap: int

    @property
    def overflow(self) -> int:
        return self.required - self.cap


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
    # Metadata only — `read_skill` renders this as the file manifest (AC-18) and reads
    # `scan_status` off it for AC-34's gate, so both answers come from the snapshot the
    # tap already validated rather than a query at tool-call time. The bytes stay in
    # MinIO until a `path` is actually asked for.
    files: Mapping[uuid.UUID, tuple[SkillFile, ...]] = field(default_factory=dict)

    def files_for(self, skill_id: uuid.UUID) -> tuple[SkillFile, ...]:
        return self.files.get(skill_id, ())


class BindingService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._skills = SkillRepository(db)
        self._bindings = SkillBindingRepository(db)
        self._files = SkillFileRepository(db)
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
                raise SkillScopeMismatch("agent_scope_mismatch")
            return

        if skill.scope is SkillScope.PROJECT:
            if skill.project_id != agent_project_id:
                raise SkillScopeMismatch("project_scope_mismatch")
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
            raise SkillScopeMismatch("org_scope_mismatch")

    # -- requirements (Q-9 / [R31.09]) ---------------------------------------

    async def assert_requirements(self, skill: Skill, agent_id: uuid.UUID, *, has_scripts: bool) -> None:
        """Re-check one skill's `requires:` against the agent's enabled tools.

        Checked at bind *and* every turn. The reviewed design condemned bind-time-only
        authorization and then made `requires:` exactly that: disabling code_exec after
        a bind would leave the model reading a SKILL.md whose script cannot run, and
        confabulating (AC-35).

        `has_scripts` is a required argument rather than a lookup, because the two callers
        learn it differently: `bind` asks about one skill, the turn-time tap asks about a
        whole bound set in one query. A `None`-means-go-look default would make the N+1 the
        easy path and the batch the exception.

        The **single-skill** entry point, for `bind`. `resolve_bound_set` fetches the tool
        set once and calls `assert_requirements_against` directly — see `_enabled_tools`.
        """
        needed = self._required_tool_types(skill, has_scripts=has_scripts)
        if not needed:
            return
        self.assert_requirements_against(
            skill, has_scripts=has_scripts, enabled=await self._enabled_tools(agent_id)
        )

    async def _enabled_tools(self, agent_id: uuid.UUID) -> set[AgentToolType]:
        """The agent's enabled tool types — one query.

        Hoisted out of `assert_requirements` because the tap calls it per skill and this is
        a real DB read (`AgentsFacade.list_agent_tools`). It used to be *reachable* per
        skill but almost never *reached*: `requires` is SMAP-invented and appears in 0 of
        42 real bundles (Q-29), so `needed` was near-always empty and the short-circuit
        above skipped the query. Deriving `code_exec` from `scripts/` inverted that — every
        script-bearing skill now needs a check — so the guard that used to make this free
        would have turned it into one query per bound skill per turn.
        """
        return {t.tool_type for t in await self._agents.list_agent_tools(agent_id) if t.enabled}

    def assert_requirements_against(
        self, skill: Skill, *, has_scripts: bool, enabled: set[AgentToolType]
    ) -> None:
        """The pure half: prove `skill`'s requirements against an already-fetched tool set."""
        needed = self._required_tool_types(skill, has_scripts=has_scripts)
        for name, tool_type in needed.items():
            if tool_type not in enabled:
                raise SkillRequiresToolMissing(name, skill_name=skill.name)

    @staticmethod
    def _required_tool_types(skill: Skill, *, has_scripts: bool) -> dict[str, AgentToolType]:
        """Map the declaration to tool types, ignoring names outside the vocabulary.

        Unknown names are a compatibility warning at import, not a hard failure here
        (§8 threat 6) — but they are also never silently *satisfiable*: an unknown name
        maps to no tool, so it cannot be used to claim a requirement is met.

        **Derived, then unioned with the declaration** (AC-20 / [R31.09]). A `scripts/`
        file means the skill needs the interpreter whether or not `SKILL.md` says so, and
        the declaration alone would make the gate *cooperative*: omitting `requires:` — or
        importing a foreign bundle that never had it, since `requires` is SMAP-invented and
        appears in 0 of 42 real skills (Q-29) — would bypass it entirely. On the room path,
        staging is gated on this same tool, so without derivation the model reads a
        SKILL.md whose script was never staged and confabulates around the absence, which
        is Q-9's whole objection. The union direction matters: derivation can only ever
        *add* a requirement, so a declaration can tighten the gate but never loosen it.

        **"The room path" is not a hedge.** `run_input_turn` (the headless/A2A path) never
        calls `_stage_workspace_inputs` at all, so there a script-bearing skill passes this
        gate and is *still* never staged — Q-9's confabulation, reached by a different
        route that derivation cannot close. FU-42; do not read this gate as a promise that
        a bound script exists on disk.
        """
        names = set(skill.requires)
        if has_scripts:
            names.add(DERIVED_SCRIPT_REQUIREMENT)
        return {n: REQUIRES_VOCABULARY[n] for n in sorted(names) if n in REQUIRES_VOCABULARY}

    async def _has_scripts(self, skill_id: uuid.UUID) -> bool:
        return bool(await self._files.skill_ids_with_scripts([skill_id]))

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

    async def assert_cap_fits_current_index(self, agent_id: uuid.UUID, cap: int | None) -> None:
        """AC-6's third path: the skills did not change, the budget did.

        Called by the agents context before it writes `agents.skill_index_token_cap`,
        because the DB CHECK only bounds the value at 16000 — it cannot know what the
        agent has bound. Without this the cap is accepted and then silently violated on
        every turn, since [R31.14] leaves no runtime truncation to absorb it.

        `None` means "clear the override", which is not a way out: the default is a cap
        like any other and is resolved here rather than deferred, since the old row value
        is still in the DB at this point and would be the wrong thing to check against.
        """
        await self.assert_index_fits(
            agent_id, cap_override=cap if cap is not None else DEFAULT_SKILL_INDEX_TOKEN_CAP
        )

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
    ) -> tuple[IndexBreach, ...]:
        """Agents whose index would overflow if `skill` took its new description.

        Drives the update path's rejection: lengthening a description is a write to the
        *skill*, but the budget it can break belongs to every *agent* bound to it, so the
        check fans out and the error names them.

        Returns each breach's own arithmetic rather than a bare id list. The caller has to
        put a `required`/`cap` pair in the 422, and it cannot reconstruct either one from
        an id: `required` is that agent's whole rendered index (not this skill's line), and
        `cap` is that agent's own override. Recomputing them caller-side is what produced
        a rejection reading "needs 131 tokens, cap is 2266".
        """
        return await self._breaches(
            await self._bindings.list_agent_ids_bound_to(skill.id),
            replacing=skill,
            cap_override=cap_override,
        )

    async def agents_over_index_cap_on_restore(self, skill: Skill) -> tuple[IndexBreach, ...]:
        """Agents whose index would overflow if `skill`'s cascaded bindings came back.

        Fans out over the bindings restore will actually re-attach — the cascaded ones —
        not every binding the skill ever had: one the user unbound explicitly stays
        unbound (AC-37), so charging its agent for a skill it will not receive would
        block a restore that fits.

        `adding=` rather than `replacing=`, because a soft-deleted skill is absent from
        every live bound set: it is joining them, not changing within them.
        """
        return await self._breaches(
            await self._bindings.list_agent_ids_cascade_unbound_from(skill.id),
            adding=skill,
        )

    async def _breaches(
        self,
        agent_ids: Sequence[uuid.UUID],
        *,
        adding: Skill | None = None,
        replacing: Skill | None = None,
        cap_override: int | None = None,
    ) -> tuple[IndexBreach, ...]:
        """Run `assert_index_fits` over a set of agents and collect what it refused.

        Every path that can breach the budget (bind, update, restore, cap-lowering) goes
        through the one predicate; this only chooses which agents to ask and keeps each
        refusal's own numbers instead of discarding them to a bare id.
        """
        breaches: list[IndexBreach] = []
        for agent_id in agent_ids:
            try:
                await self.assert_index_fits(
                    agent_id, adding=adding, replacing=replacing, cap_override=cap_override
                )
            except SkillIndexBudgetExceeded as exc:
                breaches.append(IndexBreach(agent_id=agent_id, required=exc.required, cap=exc.cap))
        return tuple(breaches)

    async def bind(self, *, skill_id: uuid.UUID, agent_id: uuid.UUID) -> Skill:
        """Bind after proving containment, requirements, name freedom, and budget."""
        skill = await self.resolve_bindable(skill_id, agent_id)
        await self.assert_requirements(skill, agent_id, has_scripts=await self._has_scripts(skill_id))
        await self.assert_name_free_in_bound_set(agent_id, skill.name, excluding_skill_id=skill.id)
        await self.assert_index_fits(agent_id, adding=skill)
        await self._bindings.bind(agent_id=agent_id, skill_id=skill_id)
        return skill

    async def unbind(self, *, skill_id: uuid.UUID, agent_id: uuid.UUID) -> Skill | None:
        """Unbind, returning what was unbound. `None` means **nothing was bound**.

        Returns the Skill rather than a bool so the caller can audit *which bytes* the
        agent lost without a second scope-free lookup by id. The read is not a new
        authority: the binding it just cleared is itself the proof of association.

        `include_deleted=True` keeps that `None` unambiguous. The row is guaranteed to
        exist once the unbind matched, but a filtered read would hand back `None` for a
        soft-deleted skill and make the caller report "not bound" about a binding it had
        just removed — one return value denoting two opposite outcomes.
        """
        if not await self._bindings.unbind(agent_id=agent_id, skill_id=skill_id):
            return None
        return await self._skills.get(skill_id, include_deleted=True)

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

        candidates = await self._bindings.list_live_for_agent(agent_id)
        # Two queries for the whole set, before the loop — never per skill. `requires:` is
        # derived per skill (AC-20), so both inputs it needs are batched here: which skills
        # own a script, and which tools the agent has. This is the hottest path in the
        # product and it runs for every bound skill on every turn.
        with_scripts = await self._files.skill_ids_with_scripts([s.id for s in candidates])
        # Lazily, because an agent whose skills need nothing must still pay nothing: before
        # derivation the near-universal empty `requires` made the tool read free, and this
        # keeps that true rather than trading one N+1 for one unconditional query.
        needs_tools = any(self._required_tool_types(s, has_scripts=s.id in with_scripts) for s in candidates)
        enabled = await self._enabled_tools(agent_id) if needs_tools else set()

        for skill in candidates:
            try:
                await self._assert_contains(skill, agent_id=agent_id, agent_project_id=agent_project_id)
                self.assert_requirements_against(skill, has_scripts=skill.id in with_scripts, enabled=enabled)
            except SkillContainmentFailed as exc:
                dropped.append(DroppedSkill(skill_id=skill.id, name=skill.name, reason=exc.reason))
                continue
            except SkillRequiresToolMissing as exc:
                dropped.append(
                    DroppedSkill(skill_id=skill.id, name=skill.name, reason=f"requires:{exc.tool}")
                )
                continue
            kept.append(skill)

        kept, collided = _split_name_collisions(kept)
        dropped.extend(collided)
        # After the collision split, so a dropped skill's files are never fetched or
        # rendered: a skill that is not in the snapshot has no manifest.
        files = await self._files.list_for_skills([s.id for s in kept])
        return BoundSet(
            skills=tuple(kept),
            dropped=tuple(dropped),
            files={sid: tuple(fs) for sid, fs in files.items()},
        )


def _split_name_collisions(skills: list[Skill]) -> tuple[list[Skill], list[DroppedSkill]]:
    """Separate the uniquely-named skills from every side of a name collision.

    `assert_name_free_in_bound_set` is a check-then-act with no database backstop — the
    rule spans `agent_skills` and `skills`, so no constraint can express it, and two
    concurrent binds of two same-named skills both pass the SELECT and both commit. Q-30's
    uniqueness is therefore an invariant that can be violated, and §8's cross-scope
    shadowing (a project `deploy` over the admin's platform `deploy`) re-enters through
    that window.

    **Every side is dropped, not all-but-one.** The alternatives are worse: `read_skill`'s
    name→skill dict is last-wins, so keeping one silently serves an arbitrary body under a
    name the operator believes means something specific — the exact confusion the
    invariant exists to prevent, now with a deterministic tiebreak making it repeatable
    rather than correct. There is no principled winner: preferring the broader scope lets
    a platform skill mask a project one, preferring the narrower is the shadowing attack
    verbatim. Serving neither is the only answer that is not a guess, it is per-skill
    (the agent's other bindings are untouched), it is audited, and the operator can fix it
    by unbinding one.
    """
    by_name: dict[str, list[Skill]] = {}
    for skill in skills:
        by_name.setdefault(skill.name, []).append(skill)

    kept: list[Skill] = []
    collided: list[DroppedSkill] = []
    for name, group in by_name.items():
        if len(group) == 1:
            kept.append(group[0])
            continue
        collided.extend(DroppedSkill(skill_id=s.id, name=name, reason="name_collision") for s in group)
    return kept, collided


__all__ = [
    "DEFAULT_SKILL_INDEX_TOKEN_CAP",
    "REQUIRES_VOCABULARY",
    "BindingService",
    "BoundSet",
    "DroppedSkill",
    "IndexBreach",
]
