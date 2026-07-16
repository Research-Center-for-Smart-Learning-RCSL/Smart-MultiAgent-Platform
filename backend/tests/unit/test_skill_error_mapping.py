"""Every skills domain error resolves to its intended status, and the 404 arm stays a 404.

The second half is a security pin, not housekeeping. `skill_service._assert_owned` is
explicit that this context never answers 403 on a scope mismatch, "which would confirm the
id exists to someone with no right to know" — but the bind endpoint takes a `skill_id`
with **no scope in its URL**, so `resolve_bindable` was the one place that answered
differently, handing anyone who may bind on their own agent an oracle for whether a
guessed or leaked skill id is real, live, and whose.

`SkillScopeMismatch` subclasses `SkillContainmentFailed` so the turn-time tap's
`except SkillContainmentFailed` still catches it and still audits the precise reason. That
makes the split invisible to the eye at both ends — the raise sites say `SkillScopeMismatch`
and the catch says `SkillContainmentFailed` — so the boundary behaviour is pinned here.
"""

from __future__ import annotations

import uuid

import pytest

from contexts.skills.domain import errors
from contexts.skills.interfaces.error_mapping import _MAP, _extras
from shared_kernel.errors.context_handler import resolve_spec

_SKILL_ID = uuid.uuid4()


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        (errors.SkillNotFound(_SKILL_ID), 404),
        (errors.SkillNameTaken("deploy"), 409),
        (errors.SkillRestoreConflict("deploy"), 409),
        (errors.SkillVersionMismatch(current=3, expected=2), 412),
        (errors.SkillRequiresToolMissing("code_exec", skill_name="x"), 422),
        (errors.SkillIndexBudgetExceeded(required=4000, cap=3000), 422),
        # The caller's own agent/project — leaks nothing it does not already own.
        (errors.SkillContainmentFailed("agent_gone"), 403),
        (errors.SkillContainmentFailed("project_individually_owned"), 403),
        # The skill's scope — indistinguishable from a nonexistent id.
        (errors.SkillScopeMismatch("project_scope_mismatch"), 404),
        (errors.SkillScopeMismatch("org_scope_mismatch"), 404),
        (errors.SkillScopeMismatch("agent_scope_mismatch"), 404),
    ],
)
def test_skill_errors_map_to_intended_status(exc: Exception, expected_status: int) -> None:
    _slug, status, _title = resolve_spec(exc, _MAP)
    assert status == expected_status
    assert status != 500  # never the unmapped fallback


def test_a_scope_mismatch_is_indistinguishable_from_a_missing_skill() -> None:
    # Status, slug, and title all have to match, not just the status: a 404 that says
    # "containment-failed" is the same oracle wearing a different hat.
    assert resolve_spec(errors.SkillScopeMismatch("project_scope_mismatch"), _MAP) == resolve_spec(
        errors.SkillNotFound(_SKILL_ID), _MAP
    )


def test_a_scope_mismatch_leaks_neither_a_reason_nor_a_scope_in_its_body() -> None:
    # `detail` is str(exc) in the shared handler, and `_extras` fills the rest — so both
    # have to be checked. `reason` survives on the object for the audit trail.
    exc = errors.SkillScopeMismatch("org_scope_mismatch")

    assert _extras(exc) == {}
    assert "org" not in str(exc)
    assert "mismatch" not in str(exc)
    assert exc.reason == "org_scope_mismatch"


def test_the_arms_that_describe_the_callers_own_project_still_explain_themselves() -> None:
    # These are not the oracle: the caller already holds write on this agent, so its
    # project being gone or individually owned is not news to them. A bare 403 here would
    # be a dead end for no gain.
    exc = errors.SkillContainmentFailed("project_individually_owned")

    assert _extras(exc) == {"reason": "project_individually_owned"}


def test_a_scope_mismatch_is_still_caught_as_a_containment_failure() -> None:
    # The turn-time tap catches SkillContainmentFailed and audits exc.reason. If the
    # subclass ever stops inheriting, resolve_bound_set would raise mid-turn instead of
    # dropping one skill — an availability bug wearing a refactor.
    assert issubclass(errors.SkillScopeMismatch, errors.SkillContainmentFailed)
    with pytest.raises(errors.SkillContainmentFailed) as caught:
        raise errors.SkillScopeMismatch("agent_scope_mismatch")
    assert caught.value.reason == "agent_scope_mismatch"
