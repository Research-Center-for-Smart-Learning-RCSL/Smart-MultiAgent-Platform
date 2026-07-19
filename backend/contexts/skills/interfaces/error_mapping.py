"""skills domain errors -> RFC 7807 registration (§31).

Dispatch + fallback live in ``shared_kernel.errors.context_handler``.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from contexts.skills.domain import errors
from shared_kernel.errors.context_handler import ErrorMap, register_context_handler

_MAP: ErrorMap = {
    errors.SkillNotFound: ("skills/not-found", 404, "Skill not found"),
    errors.SkillNameTaken: ("skills/name-taken", 409, "Skill name is already taken"),
    errors.SkillRestoreConflict: ("skills/restore-conflict", 409, "Restore conflicts with a live name"),
    errors.SkillVersionMismatch: ("skills/version-mismatch", 412, "Version mismatch (If-Match)"),
    errors.SkillRequiresToolMissing: (
        "skills/requires-tool-missing",
        422,
        "Agent lacks a tool this skill requires",
    ),
    errors.SkillIndexBudgetExceeded: (
        "skills/index-budget-exceeded",
        422,
        "Skill index would exceed the agent's token cap",
    ),
    errors.SkillContainmentFailed: (
        "skills/containment-failed",
        403,
        "This skill's scope does not contain the agent",
    ),
    # MRO dispatch picks the nearest mapped ancestor, so this wins over the line above for
    # the three arms that describe the *skill's* scope. Same slug, status, and title a
    # nonexistent id gets: the bind path takes a skill_id with no scope in its URL, so
    # answering "403 project_scope_mismatch" would confirm to anyone who may bind on their
    # own agent that a guessed or leaked id is real, live, and whose. `_assert_owned` has
    # refused to make that distinction from the start; this is the same rule reaching the
    # one endpoint whose shape hid it.
    errors.SkillScopeMismatch: ("skills/not-found", 404, "Skill not found"),
    errors.SkillTextRejected: (
        "skills/text-rejected",
        422,
        # Not "contains rejected characters": this class also carries the `name` pattern
        # arm, and `../evil` contains no rejected character -- dots and slashes are
        # ordinary printing characters. A title naming only the charset rule would
        # contradict the `reason` shipped beside it in the same body.
        "Skill text was rejected",
    ),
    errors.BundleInvalid: ("skills/bundle-invalid", 422, "Skill bundle is invalid"),
    errors.BundleQuarantined: ("skills/bundle-quarantined", 422, "Skill bundle failed the malware scan"),
    errors.SkillUnreadable: ("skills/unreadable", 422, "Skill has a file that is not scan-clean"),
    errors.SkillFileNotFound: ("skills/file-not-found", 404, "Skill file not found"),
    errors.SkillFilePathTaken: ("skills/file-path-taken", 409, "A file already occupies that path"),
    errors.SkillFileNotEditable: (
        "skills/file-not-editable",
        422,
        "Asset files are opaque bytes and cannot be edited as text",
    ),
    errors.SkillFileLimitExceeded: (
        "skills/file-limit-exceeded",
        422,
        "A skill may hold at most 500 files",
    ),
}


def _extras(exc: Exception) -> dict[str, Any]:
    """Members that make a rejection actionable rather than a dead end.

    `current_version` exists because prompt_studio's 412 returns a toast and leaves the
    client's version stale, which is a permanent conflict loop
    (`useConfigEditor.ts:103-113`) — deliberately not imitated. `agent_ids` exists because
    a budget or bound-set rejection is about agents the caller cannot otherwise identify:
    the write is to a skill, but the rule that refused it belongs to the agents bound to
    it.
    """
    if isinstance(exc, errors.SkillVersionMismatch):
        return {"current_version": exc.current}
    if isinstance(exc, errors.SkillIndexBudgetExceeded):
        return {
            "required": exc.required,
            "cap": exc.cap,
            "agent_ids": [str(a) for a in exc.agent_ids],
        }
    if isinstance(exc, errors.SkillNameTaken | errors.SkillRestoreConflict):
        return {"name": exc.name, "agent_ids": [str(a) for a in exc.agent_ids]}
    if isinstance(exc, errors.SkillRequiresToolMissing):
        return {"tool": exc.tool, "skill_name": exc.skill_name}
    if isinstance(exc, errors.SkillFilePathTaken):
        # `collides_with` is the whole point of the 409 when the clash is
        # case-insensitive: without it the author sees a path they believe is free
        # rejected for no visible reason.
        return {"path": exc.path, "collides_with": exc.collides_with}
    if isinstance(exc, errors.SkillFileNotEditable):
        return {"path": exc.path, "kind": exc.kind}
    if isinstance(exc, errors.SkillFileLimitExceeded):
        return {"limit": exc.limit}
    if isinstance(exc, errors.SkillTextRejected):
        # `field` alone is not actionable: the offending character is invisible in whatever
        # produced it, so `reason` carrying the codepoint is the only handle the author has.
        return {"field": exc.field, "reason": exc.reason}
    if isinstance(exc, errors.SkillScopeMismatch):
        # Before the base class, and empty: a 404 whose body names the scope that owns the
        # skill is the same oracle the status code just closed, one field down.
        return {}
    if isinstance(exc, errors.SkillContainmentFailed):
        return {"reason": exc.reason}
    if isinstance(exc, errors.BundleInvalid):
        # §10's mitigation for "editor is a textarea; frontmatter errors invisible" is
        # server-side validation "naming the offending key/line". The reason carries the
        # line; `key` and `path` are what let a UI point at the field rather than make the
        # author re-read a zip. Both are None for a whole-bundle rejection (a size cap, a
        # missing SKILL.md), which is honest: no single entry is at fault.
        return {"key": exc.key, "path": exc.path}
    if isinstance(exc, errors.BundleQuarantined):
        return {"path": exc.path}
    return {}


def register(app: FastAPI) -> None:
    register_context_handler(app, errors.SkillError, _MAP, _extras)


__all__ = ["register"]
