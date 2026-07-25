"""Unit tests for the MCP probe's tool-contract parsing (2026-07-22-mcp-tool-contract).

``_run_mcp_probe`` reads whatever the sandboxed driver prints on stdout, and that
driver may lag the backend during a rolling deploy (different image digest). These
pin the two accepted wire forms without needing a Docker daemon: the object form
the current driver emits (name/description/inputSchema) and the legacy bare-string
form an older image would still emit.
"""

from __future__ import annotations

from contexts.agents.domain.models import McpToolSpec
from contexts.agents.infrastructure.sandbox.docker_runsc import (
    _parse_probed_tool,
    _probed_tool_name,
)


def test_parses_object_form_with_schema() -> None:
    entry = {
        "name": "read_file",
        "description": "Read a file.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
    }
    spec = _parse_probed_tool(entry)
    assert spec == McpToolSpec(
        name="read_file",
        description="Read a file.",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )


def test_parses_legacy_bare_string_form() -> None:
    spec = _parse_probed_tool("read_file")
    assert spec.name == "read_file"
    assert spec.description == ""
    assert spec.input_schema == {"type": "object", "additionalProperties": True}


def test_object_form_missing_schema_falls_back_to_permissive() -> None:
    spec = _parse_probed_tool({"name": "read_file", "description": "d"})
    assert spec.input_schema == {"type": "object", "additionalProperties": True}


def test_probed_tool_name_reads_both_forms() -> None:
    assert _probed_tool_name({"name": "read_file"}) == "read_file"
    assert _probed_tool_name("read_file") == "read_file"
    assert _probed_tool_name({}) == ""
