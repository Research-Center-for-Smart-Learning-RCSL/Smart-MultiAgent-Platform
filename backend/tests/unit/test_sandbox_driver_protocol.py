"""Unit tests for the in-image driver's pure protocol helpers (K.5).

The driver lives outside the backend package (``deploy/sandbox/driver/``) and is
dependency-free by design, so we load ``protocol.py`` straight off disk and
exercise the host-contract logic — command parsing, package-reference → launch
spec, the /workspace guard, egress headers, denial detection, and result
framing — without building the image or installing the MCP SDK.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PROTOCOL_PATH = Path(__file__).resolve().parents[3] / "deploy" / "sandbox" / "driver" / "protocol.py"

# The driver lives at the repo root under deploy/, which is NOT mounted in the
# wiring tier's container (only backend/ is bind-mounted over /app). pytest still
# imports every test module during collection, so guard the module-level load:
# skip cleanly when the file is absent instead of crashing the whole collection.
if not _PROTOCOL_PATH.is_file():
    pytest.skip(
        "driver protocol.py not present (deploy/ not mounted in this tier)",
        allow_module_level=True,
    )


def _load_protocol():
    spec = importlib.util.spec_from_file_location("smap_sandbox_protocol", _PROTOCOL_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec: ``@dataclass(slots=True)`` rebuilds the class and
    # looks up ``sys.modules[cls.__module__].__dict__`` during construction.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


protocol = _load_protocol()


def test_protocol_file_exists() -> None:
    assert _PROTOCOL_PATH.is_file()


# --- command parsing -------------------------------------------------------- #


def test_parse_command() -> None:
    assert protocol.parse_command(["driver.py", "probe"]) == "probe"
    assert protocol.parse_command(["driver.py"]) == ""


# --- package reference → launch spec --------------------------------------- #


def test_npx_prefix() -> None:
    spec = protocol.parse_package_reference("npx:@modelcontextprotocol/server-everything")
    assert spec.command == "npx"
    assert spec.args == ("-y", "@modelcontextprotocol/server-everything")


def test_uvx_prefix() -> None:
    spec = protocol.parse_package_reference("uvx:mcp-server-git")
    assert spec.command == "uvx"
    assert spec.args == ("mcp-server-git",)


def test_bare_reference_defaults_to_npx() -> None:
    spec = protocol.parse_package_reference("@scope/srv")
    assert spec.command == "npx"
    assert spec.args == ("-y", "@scope/srv")


def test_extra_tokens_become_args() -> None:
    spec = protocol.parse_package_reference("uvx:mcp-server-git --repo /workspace")
    assert spec.command == "uvx"
    assert spec.args == ("mcp-server-git", "--repo", "/workspace")


def test_python_and_node_runners() -> None:
    assert protocol.parse_package_reference("python:-m pkg.server").command == "python"
    assert protocol.parse_package_reference("node:/srv/s.js").args == ("/srv/s.js",)


def test_empty_reference_raises() -> None:
    with pytest.raises(ValueError):
        protocol.parse_package_reference("")


# --- /workspace path guard -------------------------------------------------- #


def test_safe_workspace_path_relative() -> None:
    assert protocol.safe_workspace_path("notes.txt") == "/workspace/notes.txt"
    assert protocol.safe_workspace_path("/workspace/a/b") == "/workspace/a/b"


def test_safe_workspace_path_rejects_escape() -> None:
    for bad in ("../etc/passwd", "/etc/passwd", "/workspace/../secret"):
        with pytest.raises(ValueError):
            protocol.safe_workspace_path(bad)


def test_safe_workspace_path_rejects_nul_and_empty() -> None:
    with pytest.raises(ValueError):
        protocol.safe_workspace_path("a\x00b")
    with pytest.raises(ValueError):
        protocol.safe_workspace_path("")


# --- egress headers + denial detection ------------------------------------- #


def test_egress_headers() -> None:
    h = protocol.egress_headers(project_id="pid", hmac_signature="sig", target_url="https://x/y")
    assert h["x-smap-project-id"] == "pid"
    assert h["x-smap-egress-hmac"] == "sig"
    assert h["x-smap-egress-url"] == "https://x/y"


def test_egress_denied_detection() -> None:
    denied = json.dumps({"type": "urn:smap:error:mcp-egress-denied"}).encode()
    assert protocol.is_egress_denied_response(403, "application/problem+json", denied)
    # upstream 4xx (not the proxy's own denial) is NOT a denial
    assert not protocol.is_egress_denied_response(404, "application/json", b"{}")
    # 200 is never a denial
    assert not protocol.is_egress_denied_response(200, "application/problem+json", denied)
    # wrong problem type is not our denial
    other = json.dumps({"type": "urn:smap:error:something-else"}).encode()
    assert not protocol.is_egress_denied_response(403, "application/problem+json", other)


def test_frame_tools_matches_host_reader() -> None:
    # Host does json.loads(stdout).get("tools", []).
    framed = protocol.frame_tools(["a", "b"])
    assert json.loads(framed)["tools"] == ["a", "b"]


def test_exit_codes() -> None:
    assert protocol.EXIT_OK == 0
    assert protocol.EXIT_EGRESS_DENIED == 42  # the host maps 42 → McpEgressDenied
