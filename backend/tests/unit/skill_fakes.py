"""In-memory doubles for the skills repositories and the two facades it reads.

Not a test module — shared by `test_skill_service.py`, `test_skill_binding.py`,
`test_skill_index_budget.py`, `test_skill_files.py`, and `test_agent_runtime_tools.py`,
which exercise application-layer *policy* (ownership, containment, budget, the file
rules) that owns no SQL. The house pattern is `_FakeKnowledge` in
`test_agent_config_project_guard.py`: construct the service via `__new__` and hand it
doubles, so the policy under test runs without a database.

Each double mirrors its repository's **predicates**, not its SQL — notably the
live/soft-deleted filters, since every rule these tests cover is a rule about which rows
are visible. Where a predicate here diverges from the real one, the test asserting that
rule is worthless, so they are kept literal on purpose.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from contexts.agents.domain.models import AgentToolType
from contexts.skills.domain.models import (
    Skill,
    SkillBinding,
    SkillFile,
    SkillFileKind,
    SkillScanStatus,
    SkillScope,
    SkillScopeCounts,
    SkillSource,
)

# Any fixed instant: these tests assert nullness of the delete timestamps, never ordering.
NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def make_skill(
    *,
    scope: SkillScope = SkillScope.PROJECT,
    name: str = "pdf-fill",
    description: str = "Fills PDF forms.",
    body: str = "# PDF Fill\n\nSteps.",
    agent_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
    requires: tuple[str, ...] = (),
    allowed_tools: tuple[str, ...] = (),
    source: SkillSource = SkillSource.AUTHORED,
    bundle_sha256: str | None = None,
    version: int = 1,
    deleted_at: datetime | None = None,
    skill_id: uuid.UUID | None = None,
) -> Skill:
    return Skill(
        id=skill_id or uuid.uuid4(),
        scope=scope,
        agent_id=agent_id,
        project_id=project_id,
        org_id=org_id,
        name=name,
        description=description,
        body=body,
        body_sha256="0" * 64,
        source=source,
        bundle_sha256=bundle_sha256,
        requires=requires,
        allowed_tools=allowed_tools,
        extra_frontmatter={},
        created_by=uuid.uuid4(),
        version=version,
        created_at=NOW,
        deleted_at=deleted_at,
    )


def make_skill_file(
    skill_id: uuid.UUID,
    *,
    path: str = "references/guide.md",
    kind: SkillFileKind | None = None,
    scan_status: SkillScanStatus = SkillScanStatus.CLEAN,
    mime: str = "text/markdown",
    size_bytes: int = 12,
    sha256: str = "a" * 64,
    minio_key: str = "k",
) -> SkillFile:
    """A `skill_files` row. `kind` defaults to what the real `kind_for_path` would derive.

    Defaulting rather than requiring it keeps the double honest about R31.18's rule — the
    directory decides the kind, and a test that hand-picks a `kind` contradicting its own
    `path` is describing a row the write path cannot produce.
    """
    if kind is None:
        kind = {"references": SkillFileKind.REFERENCE, "scripts": SkillFileKind.SCRIPT}.get(
            path.split("/")[0], SkillFileKind.ASSET
        )
    return SkillFile(
        id=uuid.uuid4(),
        skill_id=skill_id,
        path=path,
        kind=kind,
        mime=mime,
        size_bytes=size_bytes,
        sha256=sha256,
        minio_key=minio_key,
        scan_status=scan_status,
        extracted_chars=size_bytes if kind is SkillFileKind.REFERENCE else 0,
        created_at=NOW,
    )


@dataclass
class FakeAgent:
    id: uuid.UUID
    project_id: uuid.UUID
    skill_index_token_cap: int | None = None


@dataclass
class FakeTool:
    tool_type: AgentToolType
    enabled: bool = True


@dataclass
class FakeProject:
    id: uuid.UUID
    owner_org_id: uuid.UUID | None = None
    deleted_at: datetime | None = None


class FakeAgentsFacade:
    def __init__(
        self,
        agents: dict[uuid.UUID, FakeAgent] | None = None,
        tools: dict[uuid.UUID, list[FakeTool]] | None = None,
    ) -> None:
        self.agents = agents or {}
        self.tools = tools or {}
        # `list_agent_tools` is a real DB read behind the facade, and `resolve_bound_set`
        # is the hottest path in the product, so how many times it is called is part of the
        # contract — not an implementation detail. Counted here so a test can assert it.
        self.tool_reads = 0

    async def get_agent(self, agent_id: uuid.UUID) -> FakeAgent | None:
        return self.agents.get(agent_id)

    async def list_agent_tools(self, agent_id: uuid.UUID) -> list[FakeTool]:
        self.tool_reads += 1
        return self.tools.get(agent_id, [])


class FakeTenancyFacade:
    def __init__(self, projects: dict[uuid.UUID, FakeProject] | None = None) -> None:
        self.projects = projects or {}

    async def get_project(
        self, project_id: uuid.UUID, *, include_deleted: bool = False
    ) -> FakeProject | None:
        project = self.projects.get(project_id)
        if project is None:
            return None
        if project.deleted_at is not None and not include_deleted:
            return None
        return project


class FakeSkillRepo:
    def __init__(self, skills: list[Skill] | None = None) -> None:
        self.rows: dict[uuid.UUID, Skill] = {s.id: s for s in (skills or [])}

    def put(self, skill: Skill) -> Skill:
        self.rows[skill.id] = skill
        return skill

    async def get(self, skill_id: uuid.UUID, *, include_deleted: bool = False) -> Skill | None:
        skill = self.rows.get(skill_id)
        if skill is None or (skill.deleted_at is not None and not include_deleted):
            return None
        return skill

    async def get_by_name(self, *, scope: SkillScope, owner_id: uuid.UUID | None, name: str) -> Skill | None:
        for skill in self.rows.values():
            if (
                skill.scope is scope
                and skill.owner_id == owner_id
                and skill.name == name
                and skill.deleted_at is None
            ):
                return skill
        return None

    async def list_for_scope(
        self,
        *,
        scope: SkillScope,
        owner_id: uuid.UUID | None,
        limit: int,
        offset: int,
        include_deleted: bool = False,
    ) -> tuple[list[Skill], int]:
        matched = [
            s
            for s in self.rows.values()
            if s.scope is scope and s.owner_id == owner_id and (include_deleted or s.deleted_at is None)
        ]
        matched.sort(key=lambda s: s.name)
        return matched[offset : offset + limit], len(matched)

    async def create(
        self,
        *,
        scope: SkillScope,
        agent_id: uuid.UUID | None,
        project_id: uuid.UUID | None,
        org_id: uuid.UUID | None,
        name: str,
        description: str,
        body: str,
        body_sha256: str,
        source: SkillSource,
        bundle_sha256: str | None,
        requires: tuple[str, ...],
        allowed_tools: tuple[str, ...],
        extra_frontmatter: dict[str, Any],
        created_by: uuid.UUID | None,
    ) -> Skill:
        skill = Skill(
            id=uuid.uuid4(),
            scope=scope,
            agent_id=agent_id,
            project_id=project_id,
            org_id=org_id,
            name=name,
            description=description,
            body=body,
            body_sha256=body_sha256,
            source=source,
            bundle_sha256=bundle_sha256,
            requires=tuple(requires),
            allowed_tools=tuple(allowed_tools),
            extra_frontmatter=dict(extra_frontmatter),
            created_by=created_by,
            version=1,
            created_at=NOW,
            deleted_at=None,
        )
        return self.put(skill)

    async def update(self, skill_id: uuid.UUID, values: dict[str, Any]) -> Skill | None:
        skill = self.rows.get(skill_id)
        if skill is None or skill.deleted_at is not None:
            return None
        coerced = dict(values)
        for seq_field in ("requires", "allowed_tools"):
            if seq_field in coerced:
                coerced[seq_field] = tuple(coerced[seq_field])
        # `version` is never in `values` — the DB trigger owns it (DB-5), so the double
        # bumps it here rather than letting the service look like it may pass one.
        return self.put(replace(skill, **coerced, version=skill.version + 1))

    async def soft_delete(self, skill_id: uuid.UUID) -> bool:
        skill = self.rows.get(skill_id)
        if skill is None or skill.deleted_at is not None:
            return False
        self.put(replace(skill, deleted_at=NOW))
        return True

    async def restore(self, skill_id: uuid.UUID) -> Skill | None:
        skill = self.rows.get(skill_id)
        if skill is None or skill.deleted_at is None:
            return None
        return self.put(replace(skill, deleted_at=None))

    async def cascade_owner_deleted(self, *, scope: SkillScope, owner_id: uuid.UUID) -> list[uuid.UUID]:
        hit = [
            s.id
            for s in self.rows.values()
            if s.scope is scope and s.owner_id == owner_id and s.deleted_at is None
        ]
        for skill_id in hit:
            self.put(replace(self.rows[skill_id], deleted_at=NOW))
        return hit

    async def count_by_scope(self) -> SkillScopeCounts:
        counts: dict[SkillScope, int] = {}
        for skill in self.rows.values():
            if skill.deleted_at is None:
                counts[skill.scope] = counts.get(skill.scope, 0) + 1
        return SkillScopeCounts(counts=counts)


class FakeBindingRepo:
    """Mirrors `agent_skills`, including the two-timestamp distinction.

    `deleted_at` is an explicit user unbind and `cascade_deleted_at` is one a skill's
    delete cascaded; only the latter is restorable (AC-37). Keeping them apart here is
    the whole point of the double.
    """

    def __init__(self, skills: FakeSkillRepo) -> None:
        self._skills = skills
        self.rows: dict[tuple[uuid.UUID, uuid.UUID], SkillBinding] = {}

    def seed(
        self,
        *,
        agent_id: uuid.UUID,
        skill_id: uuid.UUID,
        deleted_at: datetime | None = None,
        cascade_deleted_at: datetime | None = None,
    ) -> None:
        self.rows[(agent_id, skill_id)] = SkillBinding(
            agent_id=agent_id,
            skill_id=skill_id,
            created_at=NOW,
            deleted_at=deleted_at,
            cascade_deleted_at=cascade_deleted_at,
        )

    async def list_live_for_agent(self, agent_id: uuid.UUID) -> list[Skill]:
        out = []
        for (a_id, skill_id), binding in self.rows.items():
            if a_id != agent_id or not binding.is_live:
                continue
            skill = self._skills.rows.get(skill_id)
            # The real join filters soft-deleted skills too, not just dead bindings.
            if skill is not None and skill.deleted_at is None:
                out.append(skill)
        out.sort(key=lambda s: s.name)
        return out

    async def get(self, *, agent_id: uuid.UUID, skill_id: uuid.UUID) -> SkillBinding | None:
        return self.rows.get((agent_id, skill_id))

    async def bind(self, *, agent_id: uuid.UUID, skill_id: uuid.UUID) -> SkillBinding:
        binding = SkillBinding(
            agent_id=agent_id,
            skill_id=skill_id,
            created_at=NOW,
            deleted_at=None,
            cascade_deleted_at=None,
        )
        self.rows[(agent_id, skill_id)] = binding
        return binding

    async def unbind(self, *, agent_id: uuid.UUID, skill_id: uuid.UUID) -> bool:
        binding = self.rows.get((agent_id, skill_id))
        # Only a live binding, matching the repo: a cascade-unbound row is not bound, and
        # stamping deleted_at on it would make the cascade irreversible (AC-37).
        if binding is None or not binding.is_live:
            return False
        self.rows[(agent_id, skill_id)] = replace(binding, deleted_at=NOW)
        return True

    async def unbind_all_for_skill(self, skill_id: uuid.UUID) -> list[uuid.UUID]:
        hit = []
        for key, binding in list(self.rows.items()):
            if key[1] == skill_id and binding.is_live:
                self.rows[key] = replace(binding, cascade_deleted_at=NOW)
                hit.append(binding.agent_id)
        return hit

    async def unbind_all_for_agent(self, agent_id: uuid.UUID) -> list[uuid.UUID]:
        hit = []
        for key, binding in list(self.rows.items()):
            if key[0] == agent_id and binding.is_live:
                self.rows[key] = replace(binding, cascade_deleted_at=NOW)
                hit.append(binding.skill_id)
        return hit

    async def restore_cascaded_for_skill(self, skill_id: uuid.UUID) -> list[uuid.UUID]:
        hit = []
        for key, binding in list(self.rows.items()):
            if key[1] == skill_id and binding.cascade_deleted_at is not None:
                self.rows[key] = replace(binding, cascade_deleted_at=None)
                hit.append(binding.agent_id)
        return hit

    async def list_agent_ids_cascade_unbound_from(self, skill_id: uuid.UUID) -> list[uuid.UUID]:
        return [
            b.agent_id
            for k, b in self.rows.items()
            if k[1] == skill_id and b.cascade_deleted_at is not None and b.deleted_at is None
        ]

    async def list_agent_ids_bound_to(self, skill_id: uuid.UUID) -> list[uuid.UUID]:
        return [b.agent_id for k, b in self.rows.items() if k[1] == skill_id and b.is_live]


class FakeSkillFileRepo:
    """`SkillFileRepository` in memory.

    `skill_files` has no `deleted_at`, so `delete` really removes — the asymmetry with
    `FakeBindingRepo` above is the schema's, not this double's.
    """

    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, SkillFile] = {}

    def put(self, f: SkillFile) -> SkillFile:
        self.rows[f.id] = f
        return f

    async def list_for_skill(self, skill_id: uuid.UUID) -> list[SkillFile]:
        # Ordered by path, mirroring the real `ORDER BY path`: the manifest read_skill
        # renders is order-sensitive, so a double that returned insertion order would
        # hide a determinism bug rather than reveal it.
        return sorted(
            (f for f in self.rows.values() if f.skill_id == skill_id),
            key=lambda f: f.path,
        )

    async def get(self, file_id: uuid.UUID) -> SkillFile | None:
        return self.rows.get(file_id)

    async def list_paths_for_skill(self, skill_id: uuid.UUID) -> list[str]:
        return [f.path for f in self.rows.values() if f.skill_id == skill_id]

    async def skill_ids_with_scripts(self, skill_ids: Any) -> set[uuid.UUID]:
        ids = set(skill_ids)
        return {
            f.skill_id for f in self.rows.values() if f.skill_id in ids and f.kind is SkillFileKind.SCRIPT
        }

    async def list_for_skills(self, skill_ids: Any) -> dict[uuid.UUID, list[SkillFile]]:
        # Mirrors the real method's contract exactly, including that every requested id
        # gets a key even with no files — `resolve_bound_set` builds the manifest from
        # this, and a missing key would read as "skill absent" rather than "no files".
        ids = list(skill_ids)
        out: dict[uuid.UUID, list[SkillFile]] = {sid: [] for sid in ids}
        for f in sorted(self.rows.values(), key=lambda x: x.path):
            if f.skill_id in out:
                out[f.skill_id].append(f)
        return out

    async def create(
        self,
        *,
        skill_id: uuid.UUID,
        path: str,
        kind: SkillFileKind,
        mime: str,
        size_bytes: int,
        sha256: str,
        minio_key: str,
        scan_status: SkillScanStatus,
        extracted_chars: int,
    ) -> SkillFile:
        # The real table has UNIQUE (skill_id, path); without it here, a test proving the
        # collision check works would pass even if the check were deleted.
        if any(f.skill_id == skill_id and f.path == path for f in self.rows.values()):
            raise AssertionError(f"UNIQUE (skill_id, path) violated for {path!r}")
        f = SkillFile(
            id=uuid.uuid4(),
            skill_id=skill_id,
            path=path,
            kind=kind,
            mime=mime,
            size_bytes=size_bytes,
            sha256=sha256,
            minio_key=minio_key,
            scan_status=scan_status,
            extracted_chars=extracted_chars,
            created_at=NOW,
        )
        return self.put(f)

    async def update_content(
        self,
        file_id: uuid.UUID,
        *,
        size_bytes: int,
        sha256: str,
        minio_key: str,
        scan_status: SkillScanStatus,
        extracted_chars: int,
    ) -> SkillFile | None:
        current = self.rows.get(file_id)
        if current is None:
            return None
        return self.put(
            replace(
                current,
                size_bytes=size_bytes,
                sha256=sha256,
                minio_key=minio_key,
                scan_status=scan_status,
                extracted_chars=extracted_chars,
            )
        )

    async def mark_scan(
        self, file_id: uuid.UUID, *, scan_status: SkillScanStatus, expected_sha256: str
    ) -> bool:
        # The sha predicate is not decoration: it is what stops a scan of replaced bytes
        # writing `clean` over the replacement's `quarantined`. This module's docstring
        # says a double mirrors its repository's *predicates*, and that "where a predicate
        # here diverges from the real one, the test asserting that rule is worthless" —
        # so it is compared here rather than accepted and ignored.
        current = self.rows.get(file_id)
        if current is None or current.sha256 != expected_sha256:
            return False
        self.put(replace(current, scan_status=scan_status))
        return True

    async def delete(self, file_id: uuid.UUID) -> bool:
        return self.rows.pop(file_id, None) is not None


__all__ = [
    "NOW",
    "FakeAgent",
    "FakeAgentsFacade",
    "FakeBindingRepo",
    "FakeProject",
    "FakeSkillFileRepo",
    "FakeSkillRepo",
    "FakeTenancyFacade",
    "FakeTool",
    "make_skill",
]
