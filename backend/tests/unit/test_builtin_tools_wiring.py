"""Unit tests for the K.5 built-in / MCP tool wiring + sandbox egress signing.

Covers ``builtin_tools.build_builtin_tools`` (the first production caller of the
orphaned egress/search/sandbox factories) and ``DockerRunscSandbox._egress_env``
(the pre-signed per-project egress credential the driver receives).
"""

from __future__ import annotations

import hmac
import json
import uuid
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock

from contexts.agents.application.runtime import builtin_tools as bt
from contexts.agents.domain.mcp import SearchResult, ToolCallResult
from contexts.agents.domain.models import McpSource


def _agent() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())


def _binding(tools: tuple[str, ...]) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        source=McpSource.PACKAGE,
        reference="npx:@scope/srv",
        allowed_tools=tools,
        config={},
    )


def _deps(**over) -> bt.BuiltinToolDeps:
    base = {
        "runner": AsyncMock(),
        "proxy": object(),
        "adapters": {},
        "cache": object(),
        "rate_limiter": object(),
    }
    base.update(over)
    return bt.BuiltinToolDeps(**base)  # type: ignore[arg-type]


def _ok(stdout: str = "", *, ok: bool = True, stderr: str = "") -> ToolCallResult:
    return ToolCallResult(ok=ok, stdout=stdout, stderr=stderr, exit_code=0 if ok else 1, duration_ms=1)


# --------------------------------------------------------------------------- #
# Assembly                                                                     #
# --------------------------------------------------------------------------- #


def test_assembles_builtins_plus_mcp_tools() -> None:
    agent = _agent()
    tools = bt.build_builtin_tools(
        AsyncMock(), agent=agent, mcp_bindings=[_binding(("alpha", "beta"))], deps=_deps()
    )
    names = {t.name for t in tools}
    assert {"web_search", "code_exec", "file"} <= names
    # two MCP tools, namespaced by binding id prefix
    mcp_names = [n for n in names if n.startswith("mcp__")]
    assert len(mcp_names) == 2
    assert any(n.endswith("__alpha") for n in mcp_names)


def test_no_bindings_yields_only_builtins() -> None:
    tools = bt.build_builtin_tools(AsyncMock(), agent=_agent(), mcp_bindings=[], deps=_deps())
    assert {t.name for t in tools} == {"web_search", "code_exec", "file"}


# --------------------------------------------------------------------------- #
# code_exec / file                                                            #
# --------------------------------------------------------------------------- #


async def test_code_exec_maps_ok_and_error() -> None:
    runner = AsyncMock()
    runner.run_code_exec.return_value = _ok("42")
    tools = {t.name: t for t in bt.build_builtin_tools(AsyncMock(), agent=_agent(), mcp_bindings=[], deps=_deps(runner=runner))}

    res = await tools["code_exec"].invoke({"source": "print(42)"})
    assert res.content == "42"
    assert res.is_error is False

    runner.run_code_exec.return_value = _ok("", ok=False, stderr="boom")
    res2 = await tools["code_exec"].invoke({"source": "x"})
    assert res2.is_error is True
    assert "boom" in res2.content


async def test_code_exec_requires_source() -> None:
    tools = {t.name: t for t in bt.build_builtin_tools(AsyncMock(), agent=_agent(), mcp_bindings=[], deps=_deps())}
    res = await tools["code_exec"].invoke({})
    assert res.is_error is True


async def test_file_dispatches_op() -> None:
    runner = AsyncMock()
    runner.run_file_op.return_value = _ok("a\nb")
    tools = {t.name: t for t in bt.build_builtin_tools(AsyncMock(), agent=_agent(), mcp_bindings=[], deps=_deps(runner=runner))}

    res = await tools["file"].invoke({"op": "list", "path": "/workspace"})
    assert res.content == "a\nb"
    assert runner.run_file_op.await_args.kwargs["op"] == "list"

    bad = await tools["file"].invoke({"op": "frobnicate", "path": "/x"})
    assert bad.is_error is True


# --------------------------------------------------------------------------- #
# MCP tool                                                                      #
# --------------------------------------------------------------------------- #


async def test_mcp_tool_passes_source_reference() -> None:
    runner = AsyncMock()
    runner.invoke_mcp_tool.return_value = _ok("tool-output")
    binding = _binding(("alpha",))
    agent = _agent()
    tools = {
        t.name: t
        for t in bt.build_builtin_tools(AsyncMock(), agent=agent, mcp_bindings=[binding], deps=_deps(runner=runner))
    }
    name = next(n for n in tools if n.startswith("mcp__"))

    res = await tools[name].invoke({"q": 1})
    assert res.content == "tool-output"
    kwargs = runner.invoke_mcp_tool.await_args.kwargs
    assert kwargs["source"] == "package"
    assert kwargs["reference"] == "npx:@scope/srv"
    assert kwargs["tool_name"] == "alpha"
    assert kwargs["arguments"] == {"q": 1}


async def test_mcp_tool_degrades_on_error() -> None:
    runner = AsyncMock()
    runner.invoke_mcp_tool.side_effect = RuntimeError("daemon down")
    binding = _binding(("alpha",))
    tools = {
        t.name: t
        for t in bt.build_builtin_tools(AsyncMock(), agent=_agent(), mcp_bindings=[binding], deps=_deps(runner=runner))
    }
    name = next(n for n in tools if n.startswith("mcp__"))
    res = await tools[name].invoke({})
    assert res.is_error is True
    assert "daemon down" in res.content


# --------------------------------------------------------------------------- #
# web_search                                                                   #
# --------------------------------------------------------------------------- #


async def test_web_search_formats_results(monkeypatch) -> None:
    async def _fake_search(self, query, **kw):
        return [SearchResult(title="T", url="https://x", snippet="s", published_at=None, score=0.9)]

    monkeypatch.setattr(
        "contexts.agents.application.tools.web_search.WebSearchTool.search", _fake_search
    )
    tools = {t.name: t for t in bt.build_builtin_tools(AsyncMock(), agent=_agent(), mcp_bindings=[], deps=_deps())}
    res = await tools["web_search"].invoke({"query": "hi"})
    assert res.is_error is False
    payload = json.loads(res.content)
    assert payload[0]["url"] == "https://x"


async def test_web_search_degrades_on_missing_key() -> None:
    # Real WebSearchTool.search with no active key raises → tool returns is_error.
    tools = {t.name: t for t in bt.build_builtin_tools(AsyncMock(), agent=_agent(), mcp_bindings=[], deps=_deps())}
    res = await tools["web_search"].invoke({"query": "hi"})
    assert res.is_error is True


# --------------------------------------------------------------------------- #
# DockerRunscSandbox egress signing                                            #
# --------------------------------------------------------------------------- #


def test_sandbox_tmpfs_includes_writable_tmp() -> None:
    # Read-only rootfs: npx/uvx caches ($HOME/.npm) + matplotlib (MPLCONFIGDIR)
    # write under /tmp, so it MUST be a writable tmpfs or stdio servers and
    # `import matplotlib` fail under gVisor (K.5 audit fix).
    from contexts.agents.infrastructure.sandbox import docker_runsc as dr

    tmpfs = dr._sandbox_tmpfs()
    assert "/tmp" in tmpfs
    assert "/workspace" in tmpfs


def test_egress_env_empty_when_unconfigured() -> None:
    from contexts.agents.infrastructure.sandbox.docker_runsc import DockerRunscSandbox

    assert DockerRunscSandbox()._egress_env(uuid.uuid4()) == {}


def test_egress_env_signs_per_project() -> None:
    from contexts.agents.infrastructure.sandbox.docker_runsc import DockerRunscSandbox

    secret = b"\x01\x02\x03\x04"
    pid = uuid.uuid4()
    sandbox = DockerRunscSandbox(egress_proxy_url="http://egress-proxy:8080", egress_shared_secret=secret)
    env = sandbox._egress_env(pid)
    expected = hmac.new(secret, str(pid).encode("ascii"), sha256).hexdigest()
    assert env["SMAP_EGRESS_PROXY_URL"] == "http://egress-proxy:8080"
    assert env["SMAP_EGRESS_HMAC"] == expected


def test_sandbox_from_settings_reads_pins(monkeypatch) -> None:
    import contexts.agents.infrastructure.sandbox.docker_runsc as dr

    fake = SimpleNamespace(
        sandbox=SimpleNamespace(mcp_image="smap/mcp-runtime@sha256:abc", code_exec_image="smap/code-exec@sha256:def"),
        egress=SimpleNamespace(proxy_url="http://egress-proxy:8080", shared_secret="0a0b"),
    )
    monkeypatch.setattr("app.config.settings.get_settings", lambda: fake)
    sandbox = dr.docker_runsc_sandbox_from_settings()
    assert sandbox.mcp_image == "smap/mcp-runtime@sha256:abc"
    assert sandbox.code_exec_image == "smap/code-exec@sha256:def"
    assert sandbox.egress_shared_secret == bytes.fromhex("0a0b")
