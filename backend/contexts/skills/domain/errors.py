"""Skills domain errors — framework-free (§31).

Mapped to RFC 7807 responses in `interfaces/error_mapping.py`. `SkillNotFound` carries
the 404-not-403 rule from `prompt_studio`: a skill quoted under a scope path it does not
belong to reads as not-found, so existence never leaks across a tenant boundary.
"""

from __future__ import annotations

import uuid


class SkillError(Exception):
    """Base for every skills domain error."""


class SkillNotFound(SkillError):
    """No such skill under the quoted scope.

    Raised for both "no row" and "row exists but belongs to another scope holder" —
    never a 403. Distinguishing them would confirm the id exists to a caller with no
    right to know (`template_service.py:5-6`).
    """

    def __init__(self, skill_id: uuid.UUID | None = None) -> None:
        super().__init__(f"skill {skill_id} not found" if skill_id else "skill not found")
        self.skill_id = skill_id


class SkillNameTaken(SkillError):
    """The name collides — per scope holder ([R31.03]) or across an agent's bound set (Q-30)."""

    def __init__(self, name: str, *, agent_ids: tuple[uuid.UUID, ...] = ()) -> None:
        super().__init__(f"skill name {name!r} is already taken")
        self.name = name
        # Non-empty when the collision is a bound-set one: naming the agents is what
        # makes the 409 actionable rather than a dead end.
        self.agent_ids = agent_ids


class SkillRequiresToolMissing(SkillError):
    """The agent lacks a tool the skill requires (Q-9). 422, naming the tool."""

    def __init__(self, tool: str, *, skill_name: str | None = None) -> None:
        super().__init__(f"agent lacks required tool {tool!r}")
        self.tool = tool
        self.skill_name = skill_name


class SkillIndexBudgetExceeded(SkillError):
    """The rendered index would exceed the agent's cap ([R31.13]/[R31.14]).

    Rejected at bind, at description update, and at cap lowering — never truncated at
    turn time, because a partial index shows the model half a menu.
    """

    def __init__(self, *, required: int, cap: int, agent_ids: tuple[uuid.UUID, ...] = ()) -> None:
        super().__init__(f"skill index needs {required} tokens, cap is {cap}")
        self.required = required
        self.cap = cap
        self.agent_ids = agent_ids


class SkillContainmentFailed(SkillError):
    """The skill's scope does not contain the agent (§5's matrix, [R31.08]).

    The arms that raise *this* class describe the **caller's own** agent or project — it
    is gone, deleted, or individually owned — so `reason` reaches the client and leaks
    nothing it does not already own. The arms that describe the **skill's** scope raise
    `SkillScopeMismatch` instead; see there.

    `detail` splits the wire message from `reason` because the two have different
    audiences: `reason` is for the audit trail, `detail` is for a stranger.
    """

    def __init__(self, reason: str, *, detail: str | None = None) -> None:
        super().__init__(detail or reason)
        self.reason = reason


class SkillScopeMismatch(SkillContainmentFailed):
    """The skill is real and live, but its scope does not contain this agent. 404.

    A **subclass**, so the turn-time tap's `except SkillContainmentFailed` still catches it
    and still audits the precise `reason`, while the API boundary maps it to the same slug,
    status, and message a nonexistent id gets. Answering "403 project_scope_mismatch" tells
    a caller who may bind on their own agent that a skill id they guessed or were leaked
    exists, is live, and which scope class owns it — and `skill_service._assert_owned` is
    explicit that this context does not do that: "Every mismatch raises SkillNotFound —
    never 403, which would confirm the id exists to someone with no right to know". The
    bind path has no scope in its URL, so it was the one place that answered differently.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason, detail="Skill not found")


class SkillRestoreConflict(SkillError):
    """Restoring would collide with a live name ([R31.05]). 409."""

    def __init__(self, name: str, *, agent_ids: tuple[uuid.UUID, ...] = ()) -> None:
        super().__init__(f"restoring {name!r} conflicts with a live skill")
        self.name = name
        self.agent_ids = agent_ids


class SkillVersionMismatch(SkillError):
    """If-Match lost the optimistic-locking race. 412, carrying the current version.

    The current version travels so the client can refresh. `prompt_studio` returns a
    toast and leaves its version stale, which is a permanent conflict loop
    (`useConfigEditor.ts:103-113`) — deliberately not imitated.
    """

    def __init__(self, *, expected: int | None, current: int) -> None:
        super().__init__(f"version mismatch: expected {expected}, current {current}")
        self.expected = expected
        self.current = current


class BundleInvalid(SkillError):
    """A bundle violated the layout, limits, or frontmatter rules ([R31.19]/[R31.29])."""

    def __init__(self, reason: str, *, key: str | None = None, path: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.key = key
        self.path = path


class BundleQuarantined(SkillError):
    """A bundled file failed the malware scan; the whole bundle is rejected (Q-18)."""

    def __init__(self, path: str) -> None:
        super().__init__(f"bundle entry {path!r} was quarantined")
        self.path = path


class SkillUnreadable(SkillError):
    """A file is not `clean`, so the skill cannot be served (AC-34)."""

    def __init__(self, skill_name: str, *, path: str | None = None) -> None:
        super().__init__(f"skill {skill_name!r} has a file that is not scan-clean")
        self.skill_name = skill_name
        self.path = path


__all__ = [
    "BundleInvalid",
    "BundleQuarantined",
    "SkillContainmentFailed",
    "SkillError",
    "SkillIndexBudgetExceeded",
    "SkillNameTaken",
    "SkillNotFound",
    "SkillRequiresToolMissing",
    "SkillRestoreConflict",
    "SkillUnreadable",
    "SkillVersionMismatch",
]
