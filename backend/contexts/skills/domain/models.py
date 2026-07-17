"""Skills domain dataclasses — framework-free (§31).

A Skill is `(name, description, body, files[])` owned at exactly one of four scopes and
bound to agents explicitly. Scope is fixed at creation ([R31.02]); promoting means
copying ([R31.06]).
"""

from __future__ import annotations

import enum
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class SkillScope(str, enum.Enum):
    """Four scopes, not five.

    `user` was dropped during review (Q-26): a user is not a container, so no containment
    predicate exists for it, and the natural implementation is an always-true branch that
    lets a departed org member keep remotely-updatable code executing inside the org's
    agents. Dropping it makes the containment predicate total over every scope. FU-14
    tracks the containment-safe version.
    """

    AGENT = "agent"
    PROJECT = "project"
    ORG = "org"
    PLATFORM = "platform"


class SkillSource(str, enum.Enum):
    AUTHORED = "authored"
    IMPORTED = "imported"


class SkillFileKind(str, enum.Enum):
    """`asset` is deliberately never staged into the sandbox — see the §8 note."""

    REFERENCE = "reference"
    SCRIPT = "script"
    ASSET = "asset"


class SkillScanStatus(str, enum.Enum):
    """Mirrors the shared `rag_scan_status` PG type (0012_rag), reused by 0048 and 0056.

    Values must match that type exactly: a mismatch between this binding and the PG enum
    is an asyncpg bind error at runtime, not a startup failure.
    """

    PENDING = "pending"
    CLEAN = "clean"
    QUARANTINED = "quarantined"
    SKIPPED = "skipped"


# [R31.01]. Lowercase, hyphen-separated, <= 64 chars. Also the directory name under
# /workspace/skills/{name}/, so it must be filesystem-safe by construction.
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Anthropic's own limit. Real descriptions in the wild reach 906 characters, so a
# tighter cap would reject the corpus this format claims interchangeability with.
MAX_DESCRIPTION_CHARS = 1024


@dataclass(frozen=True, slots=True)
class Skill:
    id: uuid.UUID
    scope: SkillScope
    agent_id: uuid.UUID | None
    project_id: uuid.UUID | None
    org_id: uuid.UUID | None
    name: str
    description: str
    body: str
    body_sha256: str
    source: SkillSource
    bundle_sha256: str | None
    requires: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    extra_frontmatter: dict[str, Any]
    created_by: uuid.UUID | None
    version: int
    created_at: datetime
    deleted_at: datetime | None

    @property
    def owner_id(self) -> uuid.UUID | None:
        """The populated owner column for this scope; None for platform."""
        return {
            SkillScope.AGENT: self.agent_id,
            SkillScope.PROJECT: self.project_id,
            SkillScope.ORG: self.org_id,
            SkillScope.PLATFORM: None,
        }[self.scope]

    # NOTE: Q-20's "diverged from original bundle" badge is deliberately not a property
    # here. Divergence is `hash(current authored byte set) != bundle_sha256`, and the
    # authored byte set is defined by the exporter (Q-30), which lands with bundles in
    # Phase 4. Anything computable from these fields alone — e.g. "imported and has a
    # bundle hash" — is true of *every* imported skill and would badge them all as
    # diverged forever. Until an importer exists no row carries a bundle_sha256, so the
    # honest Phase 1 answer is False; see the derivation at the API boundary.


@dataclass(frozen=True, slots=True)
class SkillFile:
    id: uuid.UUID
    skill_id: uuid.UUID
    path: str
    kind: SkillFileKind
    mime: str
    size_bytes: int
    sha256: str
    minio_key: str
    scan_status: SkillScanStatus
    extracted_chars: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SkillRead:
    """One `read_skill` invocation, for `message.metadata` ([R31.17] / AC-19).

    `body_sha256` is the field that makes this worth recording. Bodies are mutable in
    place and there is no version tree (Q-21), so `version` alone answers "which row was
    read", never "which bytes ran" — and the two differ the moment a body is edited
    between the read and the question. Q-15/Q-27 name the hash for exactly this.

    Not an audit event: R31.25 excludes `read_skill` explicitly, because a read is part
    of a turn and the turn's message is where it belongs. That has a known cost — the
    metadata is user-erasable via MESSAGE_DELETE (#20), which is why §8's item 10 keeps
    the *mutation* trail standing alone in the audit log.
    """

    skill_id: uuid.UUID
    name: str
    scope: SkillScope
    version: int
    body_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": str(self.skill_id),
            "name": self.name,
            "scope": self.scope.value,
            "version": self.version,
            "body_sha256": self.body_sha256,
        }


@dataclass(frozen=True, slots=True)
class SkillBinding:
    agent_id: uuid.UUID
    skill_id: uuid.UUID
    created_at: datetime
    deleted_at: datetime | None
    cascade_deleted_at: datetime | None

    @property
    def is_live(self) -> bool:
        return self.deleted_at is None and self.cascade_deleted_at is None


@dataclass(frozen=True, slots=True)
class SkillDraft:
    """Create/patch payload. `None` means "unchanged" on patch.

    `name` is absent by construction, not optional: it is the key the model invokes, the
    key bindings and bundle exports are addressed by, and the directory name under
    /workspace/skills/. Renaming is a copy ([R31.06] / AC-39).
    """

    description: str | None = None
    body: str | None = None
    requires: tuple[str, ...] | None = None
    allowed_tools: tuple[str, ...] | None = None
    extra_frontmatter: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SkillScopeCounts:
    """[R31.11] — the ratio of agent-private to shared skills, so §5's premise stays auditable."""

    counts: dict[SkillScope, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


__all__ = [
    "MAX_DESCRIPTION_CHARS",
    "SKILL_NAME_RE",
    "Skill",
    "SkillBinding",
    "SkillDraft",
    "SkillFile",
    "SkillFileKind",
    "SkillRead",
    "SkillScanStatus",
    "SkillScope",
    "SkillScopeCounts",
    "SkillSource",
]
