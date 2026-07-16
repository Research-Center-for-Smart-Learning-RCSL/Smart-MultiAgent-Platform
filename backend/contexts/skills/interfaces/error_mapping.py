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
    errors.BundleInvalid: ("skills/bundle-invalid", 422, "Skill bundle is invalid"),
    errors.BundleQuarantined: ("skills/bundle-quarantined", 422, "Skill bundle failed the malware scan"),
    errors.SkillUnreadable: ("skills/unreadable", 422, "Skill has a file that is not scan-clean"),
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
    if isinstance(exc, errors.SkillContainmentFailed):
        return {"reason": exc.reason}
    return {}


def register(app: FastAPI) -> None:
    register_context_handler(app, errors.SkillError, _MAP, _extras)


__all__ = ["register"]
