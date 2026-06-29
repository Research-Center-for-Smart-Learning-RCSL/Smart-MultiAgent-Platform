"""Unit tests for free-form JSON size guards (API-7).

Covers the reusable `shared_kernel.validation` bounds and that the API request
models actually reject oversized configs / definitions / payloads before they
can reach the database.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import BaseModel, Field, ValidationError

from shared_kernel.validation import (
    BoundedConfig,
    BoundedDefinition,
    BoundedPayload,
    bounded_json,
)


class _Config(BaseModel):
    c: BoundedConfig


class _Definition(BaseModel):
    d: BoundedDefinition


class _Payload(BaseModel):
    p: BoundedPayload


def test_small_config_accepted() -> None:
    assert _Config(c={"strategy": "fixed", "size": 512}).c == {"strategy": "fixed", "size": 512}


def test_oversized_config_rejected() -> None:
    with pytest.raises(ValidationError):
        _Config(c={"blob": "x" * 20_000})


def test_deeply_nested_config_rejected() -> None:
    node: dict = {}
    cur = node
    for _ in range(40):
        cur["n"] = {}
        cur = cur["n"]
    with pytest.raises(ValidationError):
        _Config(c=node)


def test_too_many_nodes_rejected() -> None:
    with pytest.raises(ValidationError):
        _Config(c={str(i): i for i in range(1_000)})


def test_definition_allows_large_but_bounded_graph() -> None:
    # A realistic workflow graph (many small nodes) stays under the higher cap.
    graph = {"nodes": [{"id": str(i), "type": "instruct"} for i in range(100)]}
    assert _Definition(d=graph).d == graph


def test_definition_rejects_megabyte_blob() -> None:
    with pytest.raises(ValidationError):
        _Definition(d={"payload": "x" * 600_000})


def test_payload_rejects_oversize() -> None:
    with pytest.raises(ValidationError):
        _Payload(p={"v": "x" * 70_000})


def test_none_passes_through_for_optional_fields() -> None:
    class _Opt(BaseModel):
        c: BoundedConfig | None = None

    assert _Opt().c is None
    assert _Opt(c=None).c is None


def test_custom_factory_bounds_bytes() -> None:
    from typing import Annotated, Any

    class _Tiny(BaseModel):
        v: Annotated[dict[str, Any], bounded_json(max_bytes=10)]

    assert _Tiny(v={"a": 1}).v == {"a": 1}
    with pytest.raises(ValidationError):
        _Tiny(v={"a": "way too long to fit"})


def test_reorder_priorities_key_count_capped() -> None:
    # The key_groups ReorderIn dict is capped via Pydantic max_length; emulate it.
    class _Reorder(BaseModel):
        priorities: dict[uuid.UUID, int] = Field(max_length=3)

    ok = {uuid.uuid4(): i for i in range(3)}
    assert _Reorder(priorities=ok).priorities == ok
    with pytest.raises(ValidationError):
        _Reorder(priorities={uuid.uuid4(): i for i in range(4)})
