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


class SkillTextRejected(SkillError):
    """A model-or-UI-facing string failed the charset rule at the write. 422.

    Raised by `text_rules.assert_text_ok`, which is the service-layer gate every write
    crosses — not the API's Pydantic validators, which produce their own per-field 422
    with a `loc` and are kept for that reason. Reaching this class means the write came
    from somewhere other than a request body: a `copy` carrying a source row's text, or
    an importer. `field` and `reason` both travel because the offending character is by
    definition invisible in whatever produced it, so the codepoint in `reason` is the
    only actionable handle a caller has.
    """

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field} {reason}")
        self.field = field
        self.reason = reason


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


class SkillFileNotFound(SkillError):
    """No such file on this skill. 404.

    Separate from `SkillNotFound` so the two cannot be confused at the boundary, but it
    carries the same rule: the caller has already proven ownership of the *skill* to
    reach here, so this one is an honest 404 rather than a leak-avoiding one.
    """

    def __init__(self, file_id: uuid.UUID | None = None, *, path: str | None = None) -> None:
        super().__init__(f"skill file {path or file_id} not found")
        self.file_id = file_id
        self.path = path


class SkillFilePathTaken(SkillError):
    """A file already occupies this path, or one that collides with it. 409.

    `collides_with` is non-empty when the clash is case-insensitive rather than exact:
    the DB's `UNIQUE (skill_id, path)` is byte-exact, so `assets/X.md` and `assets/x.md`
    both fit it while resolving to one file on a Windows dev box. Naming the existing
    path is what tells the author why a path that looks free is not.
    """

    def __init__(self, path: str, *, collides_with: str | None = None) -> None:
        super().__init__(
            f"path {path!r} collides with existing {collides_with!r}"
            if collides_with and collides_with != path
            else f"path {path!r} is already taken"
        )
        self.path = path
        self.collides_with = collides_with


class SkillFileLimitExceeded(SkillError):
    """The skill already holds `MAX_SKILL_FILES` files ([R31.19]'s entry limit). 422.

    The same 500 a bundle may carry, applied to the per-file API so a skill assembled one
    upload at a time cannot exceed what the same skill could carry as a bundle.
    """

    def __init__(self, *, limit: int) -> None:
        super().__init__(f"a skill may hold at most {limit} files")
        self.limit = limit


class SkillFileNotEditable(SkillError):
    """An `asset` is opaque bytes and has no text to edit (AC-17 / [R31.18]). 422.

    `kind` gates the editor rather than mime: a PNG mislabelled `text/plain` is still an
    asset, and it is the *declared kind* that decides whether `read_skill` will ever
    render its contents.
    """

    def __init__(self, path: str, *, kind: str) -> None:
        super().__init__(f"file {path!r} is a {kind} and cannot be edited as text")
        self.path = path
        self.kind = kind


__all__ = [
    "BundleInvalid",
    "BundleQuarantined",
    "SkillContainmentFailed",
    "SkillError",
    "SkillFileLimitExceeded",
    "SkillFileNotEditable",
    "SkillFileNotFound",
    "SkillFilePathTaken",
    "SkillIndexBudgetExceeded",
    "SkillNameTaken",
    "SkillNotFound",
    "SkillRequiresToolMissing",
    "SkillRestoreConflict",
    "SkillTextRejected",
    "SkillUnreadable",
    "SkillVersionMismatch",
]
