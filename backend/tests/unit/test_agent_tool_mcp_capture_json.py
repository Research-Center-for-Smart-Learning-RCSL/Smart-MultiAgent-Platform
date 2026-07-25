"""Unit tests for the AgentTool.mcp_captured_tools JSON <-> McpToolSpec mapping.

Pure round-trip coverage for the repository's serialisation helpers
(2026-07-22-mcp-tool-contract, migration 0066) -- no DB required.
"""

from __future__ import annotations

from contexts.agents.domain.models import McpToolSpec
from contexts.agents.infrastructure.repositories import (
    _captured_tools_from_json,
    _captured_tools_to_json,
)


def test_round_trips_a_captured_spec() -> None:
    specs = (
        McpToolSpec(
            name="read_file",
            description="Read a file.",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        ),
    )
    raw = _captured_tools_to_json(specs)
    assert raw == [
        {
            "name": "read_file",
            "description": "Read a file.",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
    ]
    assert _captured_tools_from_json(raw) == specs


def test_from_json_tolerates_missing_or_malformed_input() -> None:
    assert _captured_tools_from_json(None) == ()
    assert _captured_tools_from_json([]) == ()
    assert _captured_tools_from_json("not a list") == ()
    # A non-dict entry in the list is skipped rather than crashing the mapper.
    parsed = _captured_tools_from_json([{"name": "a"}, "garbage", {"name": "b", "input_schema": "nope"}])
    assert [s.name for s in parsed] == ["a", "b"]
    assert parsed[1].input_schema == {"type": "object", "additionalProperties": True}
