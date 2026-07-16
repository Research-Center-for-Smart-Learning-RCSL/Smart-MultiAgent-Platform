"""Skill CRUD, ownership, soft-delete cascade, and restore (§31).

AC-1  — create / read / update / soft-delete / restore at each of the four scopes.
AC-2  — a skill quoted under a scope path it does not belong to is 404, never 403.
AC-8  — soft-delete unbinds in the same transaction; CASCADE is not the mechanism.
AC-9  — a body edit lands on the next turn, not the one already running.
AC-15 — per-scope counts for the admin metric.
AC-19 — `skill.updated` carries body_sha256 before -> after.
AC-39 — `name` is unpatchable by construction; restore re-checks bound-set uniqueness.

The doubles are `skill_fakes`; see its module docstring for why policy is tested without
a database.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from contexts.skills.application.binding_service import BindingService
from contexts.skills.application.skill_service import SkillService, body_sha256
from contexts.skills.domain.errors import (
    SkillNameTaken,
    SkillNotFound,
    SkillRestoreConflict,
    SkillVersionMismatch,
)
from contexts.skills.domain.models import SkillDraft, SkillScope, SkillSource
from shared_kernel import audit
from tests.unit.skill_fakes import (
    FakeAgent,
    FakeAgentsFacade,
    FakeBindingRepo,
    FakeSkillRepo,
    FakeTenancyFacade,
    make_skill,
)

ACTOR = uuid.uuid4()


class _Harness:
    """A SkillService wired to doubles, plus the audit events it emitted."""

    def __init__(self) -> None:
        self.skills = FakeSkillRepo()
        self.bindings = FakeBindingRepo(self.skills)
        self.agents = FakeAgentsFacade()
        self.tenancy = FakeTenancyFacade()
        self.events: list[audit.AuditEvent] = []

        rules = BindingService.__new__(BindingService)
        rules._db = None  # type: ignore[attr-defined]
        rules._skills = self.skills  # type: ignore[attr-defined]
        rules._bindings = self.bindings  # type: ignore[attr-defined]
        rules._agents = self.agents  # type: ignore[attr-defined]
        rules._tenancy = self.tenancy  # type: ignore[attr-defined]

        svc = SkillService.__new__(SkillService)
        svc._db = None  # type: ignore[attr-defined]
        svc._skills = self.skills  # type: ignore[attr-defined]
        svc._bindings = self.bindings  # type: ignore[attr-defined]
        svc._binding_rules = rules  # type: ignore[attr-defined]
        self.svc = svc
        self.rules = rules

    def actions(self) -> list[str]:
        return [e.action for e in self.events]

    def last(self) -> audit.AuditEvent:
        return self.events[-1]


@pytest.fixture
def h(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    harness = _Harness()

    async def _capture(_db: Any, event: audit.AuditEvent) -> None:
        harness.events.append(event)

    monkeypatch.setattr(audit, "emit", _capture)
    return harness


def _owner_for(scope: SkillScope) -> uuid.UUID | None:
    """Platform owns nothing; the other three are keyed on their holder's id."""
    return None if scope is SkillScope.PLATFORM else uuid.uuid4()


ALL_SCOPES = list(SkillScope)


# -- AC-1: the lifecycle, at each of the four scopes -------------------------


@pytest.mark.parametrize("scope", ALL_SCOPES)
async def test_create_populates_only_this_scopes_owner_column(h: _Harness, scope: SkillScope) -> None:
    owner = _owner_for(scope)
    skill = await h.svc.create(
        scope=scope,
        owner_id=owner,
        name="pdf-fill",
        description="Fills PDF forms.",
        body="# body",
        actor_user_id=ACTOR,
    )

    assert skill.scope is scope
    assert skill.owner_id == owner
    # ck_skill_scope's XOR shape: exactly one of the three columns is set (none for
    # platform), and it is the one this scope names.
    populated = [c for c in (skill.agent_id, skill.project_id, skill.org_id) if c is not None]
    assert populated == ([owner] if scope is not SkillScope.PLATFORM else [])
    assert skill.body_sha256 == body_sha256("# body")
    assert h.actions() == ["skill.created"]


@pytest.mark.parametrize("scope", ALL_SCOPES)
async def test_full_lifecycle_at_every_scope(h: _Harness, scope: SkillScope) -> None:
    owner = _owner_for(scope)
    created = await h.svc.create(
        scope=scope,
        owner_id=owner,
        name="pdf-fill",
        description="Fills PDF forms.",
        body="v1",
        actor_user_id=ACTOR,
    )

    read = await h.svc.get_owned(created.id, scope, owner_id=owner)
    assert read.id == created.id

    updated = await h.svc.update(
        created.id,
        scope,
        owner_id=owner,
        draft=SkillDraft(body="v2"),
        expected_version=created.version,
        actor_user_id=ACTOR,
    )
    assert updated.body == "v2"

    unbound = await h.svc.soft_delete(
        created.id, scope, owner_id=owner, expected_version=None, actor_user_id=ACTOR
    )
    assert unbound == []
    with pytest.raises(SkillNotFound):
        await h.svc.get_owned(created.id, scope, owner_id=owner)

    restored = await h.svc.restore(created.id, scope, owner_id=owner, actor_user_id=ACTOR)
    assert restored.deleted_at is None
    assert (await h.svc.get_owned(created.id, scope, owner_id=owner)).id == created.id
    assert h.actions() == ["skill.created", "skill.updated", "skill.deleted", "skill.restored"]


async def test_create_rejects_a_duplicate_name_in_the_same_scope_holder(h: _Harness) -> None:
    owner = uuid.uuid4()
    await h.svc.create(
        scope=SkillScope.PROJECT,
        owner_id=owner,
        name="pdf-fill",
        description="d",
        body="b",
        actor_user_id=ACTOR,
    )
    with pytest.raises(SkillNameTaken):
        await h.svc.create(
            scope=SkillScope.PROJECT,
            owner_id=owner,
            name="pdf-fill",
            description="d",
            body="b",
            actor_user_id=ACTOR,
        )


async def test_same_name_is_free_under_a_different_scope_holder(h: _Harness) -> None:
    """[R31.03] keys uniqueness on the holder, not the name — Q-30 catches the rest."""
    for _ in range(2):
        await h.svc.create(
            scope=SkillScope.PROJECT,
            owner_id=uuid.uuid4(),
            name="pdf-fill",
            description="d",
            body="b",
            actor_user_id=ACTOR,
        )
    assert len(h.skills.rows) == 2


async def test_update_with_a_stale_version_is_a_mismatch_carrying_the_current_one(h: _Harness) -> None:
    owner = uuid.uuid4()
    skill = await h.svc.create(
        scope=SkillScope.PROJECT, owner_id=owner, name="s", description="d", body="b", actor_user_id=ACTOR
    )
    with pytest.raises(SkillVersionMismatch) as exc:
        await h.svc.update(
            skill.id,
            SkillScope.PROJECT,
            owner_id=owner,
            draft=SkillDraft(body="b2"),
            expected_version=skill.version + 7,
            actor_user_id=ACTOR,
        )
    assert exc.value.current == skill.version


async def test_an_empty_draft_is_a_no_op_and_emits_nothing(h: _Harness) -> None:
    owner = uuid.uuid4()
    skill = await h.svc.create(
        scope=SkillScope.PROJECT, owner_id=owner, name="s", description="d", body="b", actor_user_id=ACTOR
    )
    h.events.clear()

    same = await h.svc.update(
        skill.id,
        SkillScope.PROJECT,
        owner_id=owner,
        draft=SkillDraft(),
        expected_version=None,
        actor_user_id=ACTOR,
    )
    assert same.version == skill.version
    assert h.events == []


# -- AC-2: 404, never 403, for all four scopes x foreign owner ---------------


@pytest.mark.parametrize("scope", [s for s in ALL_SCOPES if s is not SkillScope.PLATFORM])
async def test_foreign_owner_reads_as_not_found(h: _Harness, scope: SkillScope) -> None:
    """Capability != target: holding the cap for org X never proves this skill is in X."""
    skill = await h.svc.create(
        scope=scope,
        owner_id=uuid.uuid4(),
        name="s",
        description="d",
        body="b",
        actor_user_id=ACTOR,
    )
    with pytest.raises(SkillNotFound):
        await h.svc.get_owned(skill.id, scope, owner_id=uuid.uuid4())


@pytest.mark.parametrize("scope", ALL_SCOPES)
async def test_a_skill_quoted_under_the_wrong_scope_reads_as_not_found(
    h: _Harness, scope: SkillScope
) -> None:
    owner = _owner_for(scope)
    skill = await h.svc.create(
        scope=scope, owner_id=owner, name="s", description="d", body="b", actor_user_id=ACTOR
    )
    for other in ALL_SCOPES:
        if other is scope:
            continue
        with pytest.raises(SkillNotFound):
            await h.svc.get_owned(skill.id, other, owner_id=_owner_for(other))


async def test_platform_scope_ownership_check_is_not_vacuous(h: _Harness) -> None:
    """Platform's owner is None on both sides, so the id/scope pair carries the check.

    Without this, "owner_id == owner_id" comparing None to None could be mistaken for
    proof, and a platform path would reach a project-scoped skill.
    """
    project_skill = await h.svc.create(
        scope=SkillScope.PROJECT,
        owner_id=uuid.uuid4(),
        name="s",
        description="d",
        body="b",
        actor_user_id=ACTOR,
    )
    with pytest.raises(SkillNotFound):
        await h.svc.get_owned(project_skill.id, SkillScope.PLATFORM, owner_id=None)


async def test_mutations_reject_a_foreign_owner_too(h: _Harness) -> None:
    """The guard is on every path, not only the read — the write paths are the IDOR."""
    skill = await h.svc.create(
        scope=SkillScope.ORG,
        owner_id=uuid.uuid4(),
        name="s",
        description="d",
        body="b",
        actor_user_id=ACTOR,
    )
    intruder = uuid.uuid4()
    with pytest.raises(SkillNotFound):
        await h.svc.update(
            skill.id,
            SkillScope.ORG,
            owner_id=intruder,
            draft=SkillDraft(body="pwn"),
            expected_version=None,
            actor_user_id=ACTOR,
        )
    with pytest.raises(SkillNotFound):
        await h.svc.soft_delete(
            skill.id, SkillScope.ORG, owner_id=intruder, expected_version=None, actor_user_id=ACTOR
        )
    with pytest.raises(SkillNotFound):
        await h.svc.restore(skill.id, SkillScope.ORG, owner_id=intruder, actor_user_id=ACTOR)
    assert h.skills.rows[skill.id].body == "b"


# -- AC-8: delete unbinds in the same transaction ----------------------------


async def test_soft_delete_unbinds_every_agent_and_says_which(h: _Harness) -> None:
    owner = uuid.uuid4()
    skill = await h.svc.create(
        scope=SkillScope.PROJECT, owner_id=owner, name="s", description="d", body="b", actor_user_id=ACTOR
    )
    agents = [uuid.uuid4(), uuid.uuid4()]
    for a in agents:
        h.bindings.seed(agent_id=a, skill_id=skill.id)

    unbound = await h.svc.soft_delete(
        skill.id, SkillScope.PROJECT, owner_id=owner, expected_version=None, actor_user_id=ACTOR
    )

    assert sorted(map(str, unbound)) == sorted(map(str, agents))
    assert h.last().action == "skill.deleted"
    assert sorted(h.last().metadata["unbound_agent_ids"]) == sorted(map(str, agents))


async def test_deleted_skill_is_gone_from_the_next_turns_snapshot(h: _Harness) -> None:
    """The row survives (soft delete), so the snapshot must exclude it by predicate.

    A FK CASCADE cannot produce this: it fires only on a physical DELETE, never on the
    soft-delete UPDATE — so relying on it would leave the binding live and pointing at a
    deleted skill (F-18, the defect 0054 repairs).
    """
    owner = uuid.uuid4()
    agent_id = uuid.uuid4()
    skill = await h.svc.create(
        scope=SkillScope.PROJECT, owner_id=owner, name="s", description="d", body="b", actor_user_id=ACTOR
    )
    h.bindings.seed(agent_id=agent_id, skill_id=skill.id)
    assert await h.bindings.list_live_for_agent(agent_id) != []

    await h.svc.soft_delete(
        skill.id, SkillScope.PROJECT, owner_id=owner, expected_version=None, actor_user_id=ACTOR
    )

    assert await h.bindings.list_live_for_agent(agent_id) == []
    # The binding row is still there and marked cascade-unbound, not physically gone.
    binding = await h.bindings.get(agent_id=agent_id, skill_id=skill.id)
    assert binding is not None
    assert binding.cascade_deleted_at is not None
    assert binding.deleted_at is None


async def test_cascade_agent_deleted_unbinds_and_deletes_agent_scoped_skills(h: _Harness) -> None:
    """AC-38: soft-deleting an agent, from the agents context's own transaction."""
    agent_id = uuid.uuid4()
    private = h.skills.put(make_skill(scope=SkillScope.AGENT, agent_id=agent_id, name="private"))
    shared = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=uuid.uuid4(), name="shared"))
    h.bindings.seed(agent_id=agent_id, skill_id=private.id)
    h.bindings.seed(agent_id=agent_id, skill_id=shared.id)

    unbound = await h.svc.cascade_agent_deleted(agent_id)

    assert sorted(map(str, unbound)) == sorted([str(private.id), str(shared.id)])
    assert h.skills.rows[private.id].deleted_at is not None
    # The project skill outlives the agent — only the binding went.
    assert h.skills.rows[shared.id].deleted_at is None
    assert await h.bindings.list_live_for_agent(agent_id) == []


# -- AC-9: a body edit lands on the next turn --------------------------------


async def test_body_edit_does_not_mutate_a_running_turns_snapshot(h: _Harness) -> None:
    owner = uuid.uuid4()
    agent_id = uuid.uuid4()
    h.agents.agents[agent_id] = FakeAgent(id=agent_id, project_id=owner)
    skill = await h.svc.create(
        scope=SkillScope.PROJECT, owner_id=owner, name="s", description="d", body="v1", actor_user_id=ACTOR
    )
    h.bindings.seed(agent_id=agent_id, skill_id=skill.id)

    running = await h.rules.resolve_bound_set(agent_id=agent_id, agent_project_id=owner)
    assert running.skills[0].body == "v1"

    await h.svc.update(
        skill.id,
        SkillScope.PROJECT,
        owner_id=owner,
        draft=SkillDraft(body="v2"),
        expected_version=None,
        actor_user_id=ACTOR,
    )

    # The snapshot is a value captured at turn start; the edit cannot reach back into it.
    assert running.skills[0].body == "v1"
    next_turn = await h.rules.resolve_bound_set(agent_id=agent_id, agent_project_id=owner)
    assert next_turn.skills[0].body == "v2"


# -- AC-19: the update event carries both hashes -----------------------------


async def test_update_event_records_body_sha256_before_and_after(h: _Harness) -> None:
    """The body is overwritten in place and there is no version tree, so without these
    the trail proves only that skill S changed at T — never which bytes ran."""
    owner = uuid.uuid4()
    skill = await h.svc.create(
        scope=SkillScope.PROJECT, owner_id=owner, name="s", description="d", body="v1", actor_user_id=ACTOR
    )
    await h.svc.update(
        skill.id,
        SkillScope.PROJECT,
        owner_id=owner,
        draft=SkillDraft(body="v2"),
        expected_version=None,
        actor_user_id=ACTOR,
    )
    meta = h.last().metadata
    assert meta["body_sha256_before"] == body_sha256("v1")
    assert meta["body_sha256_after"] == body_sha256("v2")
    assert meta["body_sha256_before"] != meta["body_sha256_after"]


# -- AC-39: name is immutable; restore re-checks uniqueness ------------------


def test_skill_draft_has_no_name_field() -> None:
    """Renaming is a copy ([R31.06]). If `name` were patchable, bound-set uniqueness
    (Q-30) could be defeated after the fact by renaming into a collision."""
    assert "name" not in SkillDraft.__dataclass_fields__


async def test_restore_409s_when_the_name_was_taken_in_the_same_scope(h: _Harness) -> None:
    owner = uuid.uuid4()
    skill = await h.svc.create(
        scope=SkillScope.PROJECT,
        owner_id=owner,
        name="pdf-fill",
        description="d",
        body="b",
        actor_user_id=ACTOR,
    )
    await h.svc.soft_delete(
        skill.id, SkillScope.PROJECT, owner_id=owner, expected_version=None, actor_user_id=ACTOR
    )
    # The partial unique indexes exclude deleted rows, so the name was free meanwhile.
    await h.svc.create(
        scope=SkillScope.PROJECT,
        owner_id=owner,
        name="pdf-fill",
        description="d",
        body="b",
        actor_user_id=ACTOR,
    )

    with pytest.raises(SkillRestoreConflict):
        await h.svc.restore(skill.id, SkillScope.PROJECT, owner_id=owner, actor_user_id=ACTOR)
    assert h.skills.rows[skill.id].deleted_at is not None


async def test_restore_409s_naming_agents_whose_bound_set_took_the_name(h: _Harness) -> None:
    """AC-39's second rule: per-scope uniqueness cannot see across scopes, so a
    same-named org skill may have entered the bound set while this one was gone."""
    project_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    skill = await h.svc.create(
        scope=SkillScope.PROJECT,
        owner_id=project_id,
        name="pdf-fill",
        description="d",
        body="b",
        actor_user_id=ACTOR,
    )
    h.bindings.seed(agent_id=agent_id, skill_id=skill.id)
    await h.svc.soft_delete(
        skill.id, SkillScope.PROJECT, owner_id=project_id, expected_version=None, actor_user_id=ACTOR
    )

    shadow = h.skills.put(make_skill(scope=SkillScope.ORG, org_id=uuid.uuid4(), name="pdf-fill"))
    h.bindings.seed(agent_id=agent_id, skill_id=shadow.id)

    with pytest.raises(SkillRestoreConflict) as exc:
        await h.svc.restore(skill.id, SkillScope.PROJECT, owner_id=project_id, actor_user_id=ACTOR)
    assert exc.value.agent_ids == (agent_id,)


async def test_restore_rebinds_only_what_this_delete_cascaded(h: _Harness) -> None:
    """AC-37: a binding a user removed on purpose must not come back carrying a body."""
    owner = uuid.uuid4()
    cascaded, explicit = uuid.uuid4(), uuid.uuid4()
    skill = await h.svc.create(
        scope=SkillScope.PROJECT, owner_id=owner, name="s", description="d", body="b", actor_user_id=ACTOR
    )
    h.bindings.seed(agent_id=cascaded, skill_id=skill.id)
    h.bindings.seed(agent_id=explicit, skill_id=skill.id)
    await h.bindings.unbind(agent_id=explicit, skill_id=skill.id)

    await h.svc.soft_delete(
        skill.id, SkillScope.PROJECT, owner_id=owner, expected_version=None, actor_user_id=ACTOR
    )
    await h.svc.restore(skill.id, SkillScope.PROJECT, owner_id=owner, actor_user_id=ACTOR)

    assert [s.id for s in await h.bindings.list_live_for_agent(cascaded)] == [skill.id]
    assert await h.bindings.list_live_for_agent(explicit) == []
    assert h.last().metadata["rebound_agent_ids"] == [str(cascaded)]


async def test_restoring_a_live_skill_is_a_no_op(h: _Harness) -> None:
    owner = uuid.uuid4()
    skill = await h.svc.create(
        scope=SkillScope.PROJECT, owner_id=owner, name="s", description="d", body="b", actor_user_id=ACTOR
    )
    h.events.clear()
    assert (
        await h.svc.restore(skill.id, SkillScope.PROJECT, owner_id=owner, actor_user_id=ACTOR)
    ).id == skill.id
    assert h.events == []


# -- copy (Q-6) --------------------------------------------------------------


async def test_copy_detaches_from_the_source_and_emits_exactly_one_event(h: _Harness) -> None:
    """Scope is immutable (Q-5), so promoting is copying — with no provenance link and
    no bundle hash carried across, or every copy would badge as diverged forever."""
    project_id, org_id = uuid.uuid4(), uuid.uuid4()
    source = h.skills.put(
        make_skill(
            scope=SkillScope.PROJECT,
            project_id=project_id,
            name="pdf-fill",
            source=SkillSource.IMPORTED,
            bundle_sha256="a" * 64,
        )
    )

    copied = await h.svc.copy(
        source.id,
        SkillScope.PROJECT,
        owner_id=project_id,
        target_scope=SkillScope.ORG,
        target_owner_id=org_id,
        name="pdf-fill-org",
        actor_user_id=ACTOR,
    )

    assert copied.scope is SkillScope.ORG
    assert copied.org_id == org_id
    assert copied.body == source.body
    assert copied.source is SkillSource.AUTHORED
    assert copied.bundle_sha256 is None
    assert h.actions() == ["skill.copied"]
    assert h.last().metadata["source_skill_id"] == str(source.id)


async def test_copy_into_a_taken_name_is_rejected(h: _Harness) -> None:
    project_id, org_id = uuid.uuid4(), uuid.uuid4()
    source = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=project_id, name="pdf-fill"))
    h.skills.put(make_skill(scope=SkillScope.ORG, org_id=org_id, name="pdf-fill"))

    with pytest.raises(SkillNameTaken):
        await h.svc.copy(
            source.id,
            SkillScope.PROJECT,
            owner_id=project_id,
            target_scope=SkillScope.ORG,
            target_owner_id=org_id,
            name="pdf-fill",
            actor_user_id=ACTOR,
        )


# -- AC-15: the admin metric -------------------------------------------------


async def test_counts_are_per_scope_and_exclude_deleted(h: _Harness) -> None:
    """[R31.11] — the agent-private-to-shared ratio is what keeps §5's premise auditable."""
    h.skills.put(make_skill(scope=SkillScope.AGENT, agent_id=uuid.uuid4(), name="a1"))
    h.skills.put(make_skill(scope=SkillScope.AGENT, agent_id=uuid.uuid4(), name="a2"))
    h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=uuid.uuid4(), name="p1"))
    h.skills.put(make_skill(scope=SkillScope.PLATFORM, name="fleet-wide"))
    dead = h.skills.put(make_skill(scope=SkillScope.ORG, org_id=uuid.uuid4(), name="dead"))
    await h.skills.soft_delete(dead.id)

    counts = await h.skills.count_by_scope()

    assert counts.counts[SkillScope.AGENT] == 2
    assert counts.counts[SkillScope.PROJECT] == 1
    assert SkillScope.ORG not in counts.counts
    assert counts.total == 4


async def test_list_for_scope_is_owner_scoped_and_hides_deleted(h: _Harness) -> None:
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=mine, name="b-second"))
    h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=mine, name="a-first"))
    h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=theirs, name="not-mine"))
    dead = h.skills.put(make_skill(scope=SkillScope.PROJECT, project_id=mine, name="c-dead"))
    await h.skills.soft_delete(dead.id)

    rows, total = await h.svc.list_for_scope(scope=SkillScope.PROJECT, owner_id=mine, limit=50, offset=0)

    assert [s.name for s in rows] == ["a-first", "b-second"]
    assert total == 2

    with_dead, total_with_dead = await h.svc.list_for_scope(
        scope=SkillScope.PROJECT, owner_id=mine, limit=50, offset=0, include_deleted=True
    )
    assert [s.name for s in with_dead] == ["a-first", "b-second", "c-dead"]
    assert total_with_dead == 3
