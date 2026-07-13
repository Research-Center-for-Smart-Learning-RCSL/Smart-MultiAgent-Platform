"""API-boundary validation for the sampling controls (R9.18, AC-6).

The range guards live on the request Pydantic models, so out-of-range values
are rejected at the API boundary (FastAPI returns 422 from the ValidationError)
before any service code runs.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.api.v1.agents import AgentCreateIn, AgentPatchIn

_KG = uuid.uuid4()


def _create(**overrides: object) -> AgentCreateIn:
    fields: dict[str, object] = {"name": "AA", "model_hint": "openai", "key_group_id": _KG}
    fields.update(overrides)
    return AgentCreateIn(**fields)  # type: ignore[arg-type]


def test_create_accepts_reproducible_settings() -> None:
    model = _create(temperature=0.0, top_p=1.0, seed=42)
    assert model.temperature == 0.0
    assert model.top_p == 1.0
    assert model.seed == 42


def test_create_accepts_upper_bounds() -> None:
    model = _create(temperature=2.0, top_p=1.0)
    assert model.temperature == 2.0
    assert model.top_p == 1.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"temperature": 2.5},
        {"temperature": -0.1},
        {"top_p": 1.5},
        {"top_p": -0.1},
    ],
)
def test_create_rejects_out_of_range(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _create(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"temperature": 2.5},
        {"top_p": 1.5},
    ],
)
def test_patch_rejects_out_of_range(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AgentPatchIn(**overrides)  # type: ignore[arg-type]


def test_patch_accepts_in_range_and_null() -> None:
    # null clears (handled downstream); a valid value passes the range guard.
    assert AgentPatchIn(temperature=0.0, top_p=0.9, seed=7).temperature == 0.0
    assert AgentPatchIn(temperature=None, top_p=None, seed=None).seed is None
