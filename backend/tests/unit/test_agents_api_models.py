"""`context_token_cap` upper bound at the API boundary (task dossier:
docs/tasks/2026-07-16-context-token-cap-upper-bound/, AC-1/AC-2).

Mirrors `test_skills_api_models.py`'s approach: instantiate the Pydantic request model
directly and assert `ValidationError`, which is what FastAPI turns into a 422 — the
fastest and most direct way to pin the boundary without a live server.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.api.v1.agents import AgentCreateIn, AgentPatchIn
from contexts.agents.domain.models import MAX_CONTEXT_TOKEN_CAP


def _create(**overrides: object) -> AgentCreateIn:
    body: dict[str, object] = {
        "name": "agent",
        "model_hint": "claude",
        "key_group_id": uuid.uuid4(),
    }
    body.update(overrides)
    return AgentCreateIn(**body)  # type: ignore[arg-type]


# -- AC-1: POST -------------------------------------------------------------


def test_create_accepts_the_bound_value() -> None:
    agent = _create(context_token_cap=MAX_CONTEXT_TOKEN_CAP)
    assert agent.context_token_cap == MAX_CONTEXT_TOKEN_CAP


def test_create_rejects_one_above_the_bound() -> None:
    with pytest.raises(ValidationError):
        _create(context_token_cap=MAX_CONTEXT_TOKEN_CAP + 1)


def test_create_accepts_null_as_provider_default() -> None:
    agent = _create(context_token_cap=None)
    assert agent.context_token_cap is None


# -- AC-2: PATCH --------------------------------------------------------------


def test_patch_accepts_the_bound_value() -> None:
    patch = AgentPatchIn(context_token_cap=MAX_CONTEXT_TOKEN_CAP)
    assert patch.context_token_cap == MAX_CONTEXT_TOKEN_CAP


def test_patch_rejects_one_above_the_bound() -> None:
    with pytest.raises(ValidationError):
        AgentPatchIn(context_token_cap=MAX_CONTEXT_TOKEN_CAP + 1)


def test_patch_still_clears_to_null() -> None:
    # `clear_context_token_cap` (agents.py:339) keys off `"context_token_cap" in
    # fields and fields["context_token_cap"] is None` — the `le=` bound must not
    # turn an explicit clear into a validation error.
    patch = AgentPatchIn(context_token_cap=None)
    assert patch.model_dump(exclude_unset=True) == {"context_token_cap": None}


# -- AC-4 (docs/tasks/2026-07-27-wakeup-config-type-validation): the typed
# `wakeup_config` boundary rejects a wrong-typed or out-of-range numeric field
# with 422 naming the field, and still accepts unmodelled root keys (Q-3, forced
# by 2026-07-27-wakeup-config-key-preservation's additive merge). ------------


@pytest.mark.parametrize("bad_n", [None, "many", True, 0, 5000])
def test_patch_rejects_wrong_typed_wakeup_fields(bad_n: object) -> None:
    with pytest.raises(ValidationError, match="wakeup_config.*n"):
        AgentPatchIn(wakeup_config={"triggers": {"every_n_messages": {"n": bad_n}}})


def test_create_rejects_wrong_typed_wakeup_fields() -> None:
    with pytest.raises(ValidationError, match="wakeup_config.*n"):
        _create(wakeup_config={"triggers": {"every_n_messages": {"n": None}}})


def test_patch_accepts_unmodelled_wakeup_root_key_alongside_valid_numbers() -> None:
    patch = AgentPatchIn(
        wakeup_config={
            "triggers": {"every_n_messages": {"n": 5}},
            "designer_note": "keep me",
        }
    )
    assert patch.wakeup_config is not None
    assert patch.wakeup_config["designer_note"] == "keep me"
    assert patch.wakeup_config["triggers"]["every_n_messages"]["n"] == 5


def test_patch_rejects_wrong_typed_soft_bounds() -> None:
    with pytest.raises(ValidationError, match="soft_bounds"):
        AgentPatchIn(wakeup_config={"soft_bounds": {"n_min": "five"}})


def test_patch_rejects_out_of_range_t_minutes() -> None:
    with pytest.raises(ValidationError, match="t_minutes"):
        AgentPatchIn(wakeup_config={"triggers": {"silence_minutes": {"t_minutes": 1441}}})


# -- docs/tasks/2026-07-28-wakeup-config-validation-and-patch-semantics: the
# type-validation dossier above covered wrong-typed *leaves*, but a wrong-typed
# *container* (`triggers` or `soft_bounds` itself, not one of its fields) fell
# through `isinstance(x, dict)` guards that skipped validation instead of
# rejecting the payload -- reaching `WakeupConfig.from_dict` unguarded (F-1/F-3),
# and the boolean `enabled` leaves were never type-checked at all (F-2). -------


def test_patch_rejects_a_non_dict_triggers_container() -> None:
    with pytest.raises(ValidationError, match="triggers"):
        AgentPatchIn(wakeup_config={"triggers": "x"})


def test_patch_rejects_a_non_dict_soft_bounds_container() -> None:
    with pytest.raises(ValidationError, match="soft_bounds"):
        AgentPatchIn(wakeup_config={"soft_bounds": "x"})


@pytest.mark.parametrize("trigger_key", ["every_n_messages", "silence_minutes", "call_only"])
def test_patch_rejects_a_non_bool_enabled_flag(trigger_key: str) -> None:
    with pytest.raises(ValidationError, match="enabled"):
        AgentPatchIn(wakeup_config={"triggers": {trigger_key: {"enabled": "false"}}})


def test_patch_rejects_a_non_bool_allow_self_open() -> None:
    with pytest.raises(ValidationError, match="allow_self_open"):
        AgentPatchIn(wakeup_config={"allow_self_open": "false"})


# -- T-10 (workflow-capability-enforcement spec §7.6, AC-9): `max_alive_subagents`
# is validated 1..20 at the API and required when `can_create_subagent` is true.
# `BoundedConfig` alone bounded size/depth, not shape, which is what let both
# `{}` and `{"max_alive_subagents": 0}` round-trip unvalidated before this. -----


def test_patch_rejects_max_alive_subagents_zero() -> None:
    with pytest.raises(ValidationError, match="max_alive_subagents"):
        AgentPatchIn(workflow_capabilities={"can_create_subagent": True, "max_alive_subagents": 0})


@pytest.mark.parametrize("value", [1, 3, 20])
def test_patch_accepts_max_alive_subagents_in_range(value: int) -> None:
    patch = AgentPatchIn(workflow_capabilities={"can_create_subagent": True, "max_alive_subagents": value})
    assert patch.workflow_capabilities is not None
    assert patch.workflow_capabilities["max_alive_subagents"] == value


def test_patch_rejects_max_alive_subagents_above_hard_cap() -> None:
    with pytest.raises(ValidationError, match="max_alive_subagents"):
        AgentPatchIn(workflow_capabilities={"can_create_subagent": True, "max_alive_subagents": 21})


def test_patch_defers_the_cross_field_rule_to_the_merged_config() -> None:
    """This model sees a PATCH *fragment*, and the column merges.

    It used to reject this payload outright, which was wrong in both directions:
    an agent already storing `max_alive_subagents: 3` could not be granted the
    capability, while `{"max_alive_subagents": null}` alone sailed through and
    deleted the bound from an agent whose `can_create_subagent` was true. The
    rule is now decided on the merge, in `AgentService` — see
    `test_agent_service.py`'s capability-invariant tests, which cover both.
    """
    patch = AgentPatchIn(workflow_capabilities={"can_create_subagent": True})
    assert patch.workflow_capabilities == {"can_create_subagent": True}


def test_create_rejects_can_create_subagent_true_without_max_alive_subagents() -> None:
    """On create there is nothing to merge with, so the service rejects it — the
    invariant did not move, only the layer that can decide it."""
    from contexts.agents.application.agent_service import _assert_capability_invariants
    from contexts.agents.domain.errors import WorkflowCapabilitiesInvalid

    with pytest.raises(WorkflowCapabilitiesInvalid, match="max_alive_subagents"):
        _assert_capability_invariants({"can_create_subagent": True}, field="workflow_capabilities")


def test_patch_accepts_can_create_subagent_false_with_null_max_alive_subagents() -> None:
    # The exact payload AgentDetailView.vue sends when the toggle is off — null
    # clears the key under the additive PATCH merge (shared_kernel.json_merge),
    # rather than erroring.
    patch = AgentPatchIn(workflow_capabilities={"can_create_subagent": False, "max_alive_subagents": None})
    assert patch.workflow_capabilities is not None
    assert patch.workflow_capabilities["max_alive_subagents"] is None


def test_create_accepts_capable_agent_with_max_alive_subagents() -> None:
    agent = _create(
        workflow_capabilities={
            "can_instruct": True,
            "can_approve": True,
            "can_create_subagent": True,
            "max_alive_subagents": 5,
        }
    )
    assert agent.workflow_capabilities["max_alive_subagents"] == 5


@pytest.mark.parametrize("cap_key", ["can_instruct", "can_approve", "can_create_subagent"])
def test_patch_rejects_a_non_bool_capability_flag(cap_key: str) -> None:
    with pytest.raises(ValidationError, match=cap_key):
        AgentPatchIn(workflow_capabilities={cap_key: "true"})
