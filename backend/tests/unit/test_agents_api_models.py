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
