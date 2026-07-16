"""The containment predicate and the per-turn snapshot (§31 §5).

AC-3  — §5's matrix at bind; an org skill cannot reach an individually-owned project.
AC-7  — a binding that goes stale mid-flight drops from the snapshot; the turn runs on.
AC-20 — `requires:` maps to the agent's enabled tools; unknown names are unsatisfiable.
AC-35 — disabling a required tool after a bind is caught by the turn-time tap.
AC-36 — re-binding is an idempotent UPSERT, not an INSERT onto a live PK.

Modeled on `test_agent_config_project_guard.py`, the SEC-H1 guard: accepting a
`skill_id` unchecked at bind is that IDOR verbatim, except it leaks *instructions into
another tenant's agent* rather than document chunks.
"""

from __future__ import annotations

import uuid

import pytest

from contexts.agents.domain.models import AgentToolType
from contexts.skills.application.binding_service import (
    REQUIRES_VOCABULARY,
    BindingService,
)
from contexts.skills.domain.errors import (
    SkillContainmentFailed,
    SkillNameTaken,
    SkillNotFound,
    SkillRequiresToolMissing,
)
from contexts.skills.domain.models import SkillScope
from tests.unit.skill_fakes import (
    NOW,
    FakeAgent,
    FakeAgentsFacade,
    FakeBindingRepo,
    FakeProject,
    FakeSkillRepo,
    FakeTenancyFacade,
    FakeTool,
    make_skill,
)


class _Harness:
    def __init__(self) -> None:
        self.skills = FakeSkillRepo()
        self.bindings = FakeBindingRepo(self.skills)
        self.agents = FakeAgentsFacade()
        self.tenancy = FakeTenancyFacade()

        svc = BindingService.__new__(BindingService)
        svc._db = None  # type: ignore[attr-defined]
        svc._skills = self.skills  # type: ignore[attr-defined]
        svc._bindings = self.bindings  # type: ignore[attr-defined]
        svc._agents = self.agents  # type: ignore[attr-defined]
        svc._tenancy = self.tenancy  # type: ignore[attr-defined]
        self.svc = svc

    def add_agent(
        self,
        *,
        project_id: uuid.UUID,
        tools: list[FakeTool] | None = None,
        cap: int | None = None,
    ) -> FakeAgent:
        agent = FakeAgent(id=uuid.uuid4(), project_id=project_id, skill_index_token_cap=cap)
        self.agents.agents[agent.id] = agent
        self.agents.tools[agent.id] = tools or []
        return agent

    def add_project(self, *, org_id: uuid.UUID | None = None, deleted: bool = False) -> FakeProject:
        project = FakeProject(id=uuid.uuid4(), owner_org_id=org_id, deleted_at=NOW if deleted else None)
        self.tenancy.projects[project.id] = project
        return project


@pytest.fixture
def h() -> _Harness:
    return _Harness()


# -- AC-3: the matrix, total over all four scopes ----------------------------


async def test_platform_skill_binds_into_any_agent(h: _Harness) -> None:
    """Platform is the constant-True row — the control over it is Q-7's explicit
    per-agent opt-in, not containment."""
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)
    skill = h.skills.put(make_skill(scope=SkillScope.PLATFORM, name="s"))

    assert (await h.svc.resolve_bindable(skill.id, agent.id)).id == skill.id


async def test_agent_skill_binds_only_into_its_own_agent(h: _Harness) -> None:
    project = h.add_project()
    mine = h.add_agent(project_id=project.id)
    other = h.add_agent(project_id=project.id)
    skill = h.skills.put(make_skill(scope=SkillScope.AGENT, agent_id=mine.id, name="s"))

    assert (await h.svc.resolve_bindable(skill.id, mine.id)).id == skill.id
    with pytest.raises(SkillContainmentFailed) as exc:
        await h.svc.resolve_bindable(skill.id, other.id)
    assert exc.value.reason == "agent_scope_mismatch"


async def test_project_skill_binds_only_into_agents_in_that_project(h: _Harness) -> None:
    mine, theirs = h.add_project(), h.add_project()
    agent = h.add_agent(project_id=mine.id)
    foreign = h.add_agent(project_id=theirs.id)
    skill = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=mine.id, name="s"))

    assert (await h.svc.resolve_bindable(skill.id, agent.id)).id == skill.id
    with pytest.raises(SkillContainmentFailed) as exc:
        await h.svc.resolve_bindable(skill.id, foreign.id)
    assert exc.value.reason == "project_scope_mismatch"


async def test_org_skill_binds_into_an_agent_in_a_project_that_org_owns(h: _Harness) -> None:
    org_id = uuid.uuid4()
    project = h.add_project(org_id=org_id)
    agent = h.add_agent(project_id=project.id)
    skill = h.skills.put(make_skill(scope=SkillScope.ORG, org_id=org_id, name="s"))

    assert (await h.svc.resolve_bindable(skill.id, agent.id)).id == skill.id


async def test_org_skill_cannot_bind_into_another_orgs_project(h: _Harness) -> None:
    project = h.add_project(org_id=uuid.uuid4())
    agent = h.add_agent(project_id=project.id)
    skill = h.skills.put(make_skill(scope=SkillScope.ORG, org_id=uuid.uuid4(), name="s"))

    with pytest.raises(SkillContainmentFailed) as exc:
        await h.svc.resolve_bindable(skill.id, agent.id)
    assert exc.value.reason == "org_scope_mismatch"


async def test_org_skill_cannot_bind_into_an_individually_owned_project(h: _Harness) -> None:
    """AC-3's named case. `projects.owner_user_id XOR owner_org_id`, so an individual
    project's `owner_org_id` is NULL — any formulation treating "no org" as a value
    would read NULL == NULL as a match and leak the org's instructions out of it."""
    project = h.add_project(org_id=None)
    agent = h.add_agent(project_id=project.id)
    skill = h.skills.put(make_skill(scope=SkillScope.ORG, org_id=uuid.uuid4(), name="s"))

    with pytest.raises(SkillContainmentFailed) as exc:
        await h.svc.resolve_bindable(skill.id, agent.id)
    assert exc.value.reason == "project_individually_owned"


async def test_org_skill_with_a_null_org_id_never_matches(h: _Harness) -> None:
    """The mirror of the case above: NULL on the *skill* side is not a wildcard either."""
    project = h.add_project(org_id=uuid.uuid4())
    agent = h.add_agent(project_id=project.id)
    skill = h.skills.put(make_skill(scope=SkillScope.ORG, org_id=None, name="s"))

    with pytest.raises(SkillContainmentFailed) as exc:
        await h.svc.resolve_bindable(skill.id, agent.id)
    assert exc.value.reason == "org_scope_mismatch"


@pytest.mark.parametrize(
    ("deleted", "reason"),
    [(True, "project_deleted"), (False, "project_gone")],
)
async def test_org_row_distinguishes_a_deleted_project_from_a_missing_one(
    h: _Harness, deleted: bool, reason: str
) -> None:
    """Why §31 does not reuse prompt_studio's `_scoping`: it collapses both of these —
    and "individually owned" — into a bare `None`, which [R31.25]'s audit event cannot
    name."""
    org_id = uuid.uuid4()
    if deleted:
        project = h.add_project(org_id=org_id, deleted=True)
        project_id = project.id
    else:
        project_id = uuid.uuid4()  # never registered with tenancy
    agent = h.add_agent(project_id=project_id)
    skill = h.skills.put(make_skill(scope=SkillScope.ORG, org_id=org_id, name="s"))

    with pytest.raises(SkillContainmentFailed) as exc:
        await h.svc.resolve_bindable(skill.id, agent.id)
    assert exc.value.reason == reason


# -- liveness is a precondition, not a matrix row ----------------------------


async def test_a_soft_deleted_skill_is_not_bindable(h: _Harness) -> None:
    """Checked first and explicitly: three of the four rows are column comparisons that
    cannot see a soft delete, and platform is constant True — so only the org row would
    fail closed on its own, and only by accident."""
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)
    skill = h.skills.put(make_skill(scope=SkillScope.PLATFORM, name="s"))
    await h.skills.soft_delete(skill.id)

    with pytest.raises(SkillNotFound):
        await h.svc.resolve_bindable(skill.id, agent.id)


async def test_binding_into_a_missing_agent_fails_closed(h: _Harness) -> None:
    skill = h.skills.put(make_skill(scope=SkillScope.PLATFORM, name="s"))
    with pytest.raises(SkillContainmentFailed) as exc:
        await h.svc.resolve_bindable(skill.id, uuid.uuid4())
    assert exc.value.reason == "agent_gone"


# -- AC-20: requires: -> the agent's enabled tools ---------------------------


async def test_requires_is_satisfied_by_an_enabled_tool(h: _Harness) -> None:
    project = h.add_project()
    agent = h.add_agent(project_id=project.id, tools=[FakeTool(AgentToolType.HOSTED_CODE_INTERPRETER)])
    skill = make_skill(name="s", requires=("code_exec",))

    await h.svc.assert_requirements(skill, agent.id)


async def test_requires_names_the_missing_tool(h: _Harness) -> None:
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)
    skill = make_skill(name="pdf-fill", requires=("code_exec",))

    with pytest.raises(SkillRequiresToolMissing) as exc:
        await h.svc.assert_requirements(skill, agent.id)
    assert exc.value.tool == "code_exec"
    assert exc.value.skill_name == "pdf-fill"


async def test_a_disabled_tool_does_not_satisfy_a_requirement(h: _Harness) -> None:
    """AC-35: the row exists but `enabled=False`, which is exactly the post-bind state
    that leaves a model reading a SKILL.md whose script cannot run — and confabulating."""
    project = h.add_project()
    agent = h.add_agent(
        project_id=project.id,
        tools=[FakeTool(AgentToolType.HOSTED_CODE_INTERPRETER, enabled=False)],
    )
    skill = make_skill(name="s", requires=("code_exec",))

    with pytest.raises(SkillRequiresToolMissing):
        await h.svc.assert_requirements(skill, agent.id)


async def test_an_unknown_requires_name_is_ignored_not_satisfiable(h: _Harness) -> None:
    """§8 threat 6: unknown names are an import-time compatibility warning, never a hard
    failure here — but they must not be *satisfiable* either, or `requires: [anything]`
    becomes a way to claim a requirement is met."""
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)
    skill = make_skill(name="s", requires=("no-such-tool",))

    await h.svc.assert_requirements(skill, agent.id)
    assert h.svc._required_tool_types(skill) == {}


def test_update_wakeup_is_deliberately_outside_the_requires_vocabulary() -> None:
    """It is added unconditionally by build_registry, so `requires: [update_wakeup]` is
    trivially satisfiable and would masquerade as a real constraint."""
    assert "update_wakeup" not in REQUIRES_VOCABULARY
    assert set(REQUIRES_VOCABULARY) == {"code_exec", "web_search", "file", "file_search"}


# -- bind / unbind -----------------------------------------------------------


async def test_bind_proves_containment_before_writing_a_row(h: _Harness) -> None:
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)
    foreign = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=uuid.uuid4(), name="s"))

    with pytest.raises(SkillContainmentFailed):
        await h.svc.bind(skill_id=foreign.id, agent_id=agent.id)
    assert h.bindings.rows == {}


async def test_rebinding_an_unbound_skill_is_an_idempotent_upsert(h: _Harness) -> None:
    """AC-36. A plain INSERT would collide with the soft-unbound row's live PK, which
    makes re-binding — the most ordinary action in the feature — fail."""
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)
    skill = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=project.id, name="s"))

    await h.svc.bind(skill_id=skill.id, agent_id=agent.id)
    assert await h.svc.unbind(skill_id=skill.id, agent_id=agent.id)
    unbound = await h.bindings.get(agent_id=agent.id, skill_id=skill.id)
    assert unbound is not None
    assert unbound.deleted_at is not None

    await h.svc.bind(skill_id=skill.id, agent_id=agent.id)

    rebound = await h.bindings.get(agent_id=agent.id, skill_id=skill.id)
    assert rebound is not None
    assert rebound.deleted_at is None
    assert rebound.cascade_deleted_at is None
    assert [s.id for s in await h.bindings.list_live_for_agent(agent.id)] == [skill.id]


async def test_binding_twice_leaves_exactly_one_row(h: _Harness) -> None:
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)
    skill = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=project.id, name="s"))

    await h.svc.bind(skill_id=skill.id, agent_id=agent.id)
    await h.svc.bind(skill_id=skill.id, agent_id=agent.id)

    assert len(h.bindings.rows) == 1


async def test_unbinding_something_not_bound_is_none_not_an_error(h: _Harness) -> None:
    assert await h.svc.unbind(skill_id=uuid.uuid4(), agent_id=uuid.uuid4()) is None


async def test_unbind_returns_the_skill_it_removed(h: _Harness) -> None:
    """The caller audits which bytes the agent lost; a bool cannot carry that."""
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)
    skill = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=project.id, name="s"))
    await h.svc.bind(skill_id=skill.id, agent_id=agent.id)

    removed = await h.svc.unbind(skill_id=skill.id, agent_id=agent.id)

    assert removed is not None
    assert removed.id == skill.id
    assert removed.body_sha256 == skill.body_sha256


async def test_unbinding_a_cascade_unbound_binding_is_a_no_op(h: _Harness) -> None:
    """It is not bound, so there is nothing to unbind — and stamping `deleted_at` on it
    would silently promote a reversible cascade into the one state restore refuses to
    undo, so the binding would never return (AC-37)."""
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)
    skill = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=project.id, name="s"))
    h.bindings.seed(agent_id=agent.id, skill_id=skill.id, cascade_deleted_at=NOW)

    assert await h.svc.unbind(skill_id=skill.id, agent_id=agent.id) is None

    binding = await h.bindings.get(agent_id=agent.id, skill_id=skill.id)
    assert binding is not None
    assert binding.deleted_at is None  # still restorable
    assert binding.cascade_deleted_at is not None


# -- Q-30: bound-set name uniqueness -----------------------------------------


async def test_a_second_skill_with_the_same_name_cannot_enter_a_bound_set(h: _Harness) -> None:
    """Fixed precedence would be the attack: a project member creates a same-named skill
    and silently shadows the admin's platform one, defeating Q-7's opt-in — which is the
    only control over platform ambient authority. `/workspace/skills/{name}/` collides
    identically."""
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)
    platform = h.skills.put(make_skill(scope=SkillScope.PLATFORM, name="pdf-fill"))
    shadow = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=project.id, name="pdf-fill"))

    await h.svc.bind(skill_id=platform.id, agent_id=agent.id)
    with pytest.raises(SkillNameTaken) as exc:
        await h.svc.bind(skill_id=shadow.id, agent_id=agent.id)
    assert exc.value.agent_ids == (agent.id,)
    assert [s.id for s in await h.bindings.list_live_for_agent(agent.id)] == [platform.id]


async def test_rebinding_the_same_skill_does_not_collide_with_itself(h: _Harness) -> None:
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)
    skill = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=project.id, name="pdf-fill"))

    await h.svc.bind(skill_id=skill.id, agent_id=agent.id)
    await h.svc.bind(skill_id=skill.id, agent_id=agent.id)  # would raise if self-excluded wrongly


async def test_the_same_name_is_free_in_a_different_agents_bound_set(h: _Harness) -> None:
    project = h.add_project()
    one, two = h.add_agent(project_id=project.id), h.add_agent(project_id=project.id)
    platform = h.skills.put(make_skill(scope=SkillScope.PLATFORM, name="pdf-fill"))
    other = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=project.id, name="pdf-fill"))

    await h.svc.bind(skill_id=platform.id, agent_id=one.id)
    await h.svc.bind(skill_id=other.id, agent_id=two.id)


# -- AC-7: the per-turn snapshot ---------------------------------------------


async def test_the_snapshot_returns_every_live_binding(h: _Harness) -> None:
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)
    for name in ("b-second", "a-first"):
        skill = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=project.id, name=name))
        h.bindings.seed(agent_id=agent.id, skill_id=skill.id)

    bound = await h.svc.resolve_bound_set(agent_id=agent.id, agent_project_id=project.id)

    # Name-ordered: the index block is rendered from this, so an unordered result would
    # make the system prompt and its token estimate vary between turns for an unchanged
    # agent.
    assert [s.name for s in bound.skills] == ["a-first", "b-second"]
    assert bound.dropped == ()


async def test_a_skill_whose_containment_went_stale_drops_and_the_turn_runs_on(h: _Harness) -> None:
    """AC-7. Copying the key-group tap's turn-skip would make revocation an availability
    attack: an agent cannot run without a key, but runs perfectly well without one of
    twenty skills."""
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)
    good = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=project.id, name="good"))
    # Bound legally, then moved out from under the agent (e.g. the agent changed project).
    stale = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=uuid.uuid4(), name="stale"))
    h.bindings.seed(agent_id=agent.id, skill_id=good.id)
    h.bindings.seed(agent_id=agent.id, skill_id=stale.id)

    bound = await h.svc.resolve_bound_set(agent_id=agent.id, agent_project_id=project.id)

    assert [s.name for s in bound.skills] == ["good"]
    assert [(d.name, d.reason) for d in bound.dropped] == [("stale", "project_scope_mismatch")]


async def test_two_skills_sharing_a_name_in_a_bound_set_both_drop(h: _Harness) -> None:
    """§8 item 7's shadowing, re-entering through the race the check-then-act leaves open.

    `assert_name_free_in_bound_set` has no database backstop — the rule spans two tables,
    so no constraint can express it, and two concurrent binds of two same-named skills
    both pass the SELECT and both commit. `read_skill`'s name->skill dict is last-wins, so
    keeping either one serves an arbitrary body under a name the operator believes means
    something specific. Neither is the only answer that is not a guess.
    """
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)
    platform = h.skills.put(make_skill(scope=SkillScope.PLATFORM, name="deploy"))
    shadow = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=project.id, name="deploy"))
    other = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=project.id, name="unrelated"))
    for s in (platform, shadow, other):
        h.bindings.seed(agent_id=agent.id, skill_id=s.id)

    bound = await h.svc.resolve_bound_set(agent_id=agent.id, agent_project_id=project.id)

    # The vetted platform skill goes too. That is the point: there is no principled
    # winner, and serving the admin's `deploy` while the operator's UI shows two is the
    # confusion the invariant exists to prevent.
    assert [s.name for s in bound.skills] == ["unrelated"]
    assert sorted((d.skill_id, d.reason) for d in bound.dropped) == sorted(
        [(platform.id, "name_collision"), (shadow.id, "name_collision")]
    )


async def test_a_collision_costs_only_the_colliding_names(h: _Harness) -> None:
    # Per-skill, like every other drop: an agent with twenty skills and one collision
    # keeps eighteen and runs.
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)
    for name in ("a", "b", "c"):
        skill = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=project.id, name=name))
        h.bindings.seed(agent_id=agent.id, skill_id=skill.id)
    for _ in range(2):
        dup = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=project.id, name="dup"))
        h.bindings.seed(agent_id=agent.id, skill_id=dup.id)

    bound = await h.svc.resolve_bound_set(agent_id=agent.id, agent_project_id=project.id)

    assert [s.name for s in bound.skills] == ["a", "b", "c"]
    assert {d.name for d in bound.dropped} == {"dup"}


async def test_a_skill_whose_required_tool_was_disabled_drops_with_a_naming_reason(h: _Harness) -> None:
    """AC-35 at the turn tap. `requires:` is re-checked every turn, not only at bind —
    the reviewed design condemned bind-time-only authorization and then made `requires:`
    exactly that."""
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)
    skill = h.skills.put(
        make_skill(scope=SkillScope.PROJECT, project_id=project.id, name="scripted", requires=("code_exec",))
    )
    h.bindings.seed(agent_id=agent.id, skill_id=skill.id)

    bound = await h.svc.resolve_bound_set(agent_id=agent.id, agent_project_id=project.id)

    assert bound.skills == ()
    assert [(d.name, d.reason) for d in bound.dropped] == [("scripted", "requires:code_exec")]


async def test_an_unbound_or_deleted_skill_is_absent_from_the_snapshot_entirely(h: _Harness) -> None:
    """Not "dropped" — dropped is for a binding that went stale, which is a warning the
    user should see. An intentional unbind is not an anomaly."""
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)
    unbound = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=project.id, name="unbound"))
    deleted = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=project.id, name="deleted"))
    h.bindings.seed(agent_id=agent.id, skill_id=unbound.id, deleted_at=NOW)
    h.bindings.seed(agent_id=agent.id, skill_id=deleted.id, cascade_deleted_at=NOW)

    bound = await h.svc.resolve_bound_set(agent_id=agent.id, agent_project_id=project.id)

    assert bound.skills == ()
    assert bound.dropped == ()


async def test_an_agent_with_nothing_bound_gets_an_empty_snapshot(h: _Harness) -> None:
    project = h.add_project()
    agent = h.add_agent(project_id=project.id)

    bound = await h.svc.resolve_bound_set(agent_id=agent.id, agent_project_id=project.id)

    assert bound.skills == ()
    assert bound.dropped == ()
