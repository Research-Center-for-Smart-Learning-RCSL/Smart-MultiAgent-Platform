"""Unit tests for the unified agent-tool wiring + sandbox egress signing.

Covers ``builtin_tools.build_agent_tools`` (dispatch from the unified
``agent_tools`` model into runtime ``Tool`` objects) and
``DockerRunscSandbox._egress_env`` (the pre-signed per-project egress
credential the driver receives).
"""

from __future__ import annotations

import hmac
import json
import uuid
from datetime import datetime
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock

from contexts.agents.application.runtime import builtin_tools as bt
from contexts.agents.domain.mcp import SearchResult, ToolCallResult
from contexts.agents.domain.models import AgentTool, AgentToolType

_NOW = datetime(2026, 6, 22, 12, 0, 0)


def _agent() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())


def _tool(
    tool_type: AgentToolType,
    *,
    enabled: bool = True,
    config: dict | None = None,
) -> AgentTool:
    return AgentTool(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        tool_type=tool_type,
        enabled=enabled,
        display_name=None,
        config=config or {},
        created_at=_NOW,
    )


def _singletons(
    *,
    web_search: bool = True,
    code_exec: bool = True,
    file: bool = True,
    file_search: bool = False,
) -> list[AgentTool]:
    """The four hosted singletons with explicit enabled flags."""
    return [
        _tool(AgentToolType.HOSTED_WEB_SEARCH, enabled=web_search),
        _tool(AgentToolType.HOSTED_CODE_INTERPRETER, enabled=code_exec),
        _tool(AgentToolType.HOSTED_FILE_WORKSPACE, enabled=file),
        _tool(AgentToolType.HOSTED_FILE_SEARCH, enabled=file_search),
    ]


def _mcp(
    allowed: tuple[str, ...],
    *,
    source: str = "package",
    reference: str = "npx:@scope/srv",
) -> AgentTool:
    return _tool(
        AgentToolType.HOSTED_MCP,
        config={"source": source, "reference": reference, "allowed_tools": list(allowed)},
    )


def _function(name: str = "lookup") -> AgentTool:
    return _tool(
        AgentToolType.LOCAL_FUNCTION,
        config={
            "name": name,
            "description": "d",
            "parameters": {"type": "object", "properties": {}},
            "http": {"method": "POST", "url": "https://api.example.com/x", "headers": {}},
        },
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


def test_assembles_singletons_plus_mcp_tools() -> None:
    agent = _agent()
    tools = bt.build_agent_tools(
        AsyncMock(), agent=agent, tools=[*_singletons(), _mcp(("alpha", "beta"))], deps=_deps()
    )
    names = {t.name for t in tools}
    assert {"web_search", "code_exec", "file"} <= names
    # two MCP tools, namespaced by tool id prefix
    mcp_names = [n for n in names if n.startswith("mcp__")]
    assert len(mcp_names) == 2
    assert any(n.endswith("__alpha") for n in mcp_names)


def test_hosted_builtin_names_are_all_reserved() -> None:
    # Drift guard: every hosted built-in tool actually built must carry a name in
    # the canonical reserved set, so a new built-in cannot be shadowed by a user
    # function that the reserved-name validation forgot to block.
    from contexts.agents.application.runtime.tool_registry import BUILTIN_TOOL_NAMES

    tools = bt.build_agent_tools(
        AsyncMock(),
        agent=_agent(),
        tools=_singletons(web_search=True, code_exec=True, file=True, file_search=True),
        deps=_deps(),
    )
    hosted = [t.name for t in tools if not t.name.startswith("mcp__")]
    assert hosted, "expected hosted built-in tools to be built"
    for name in hosted:
        assert name in BUILTIN_TOOL_NAMES, f"built-in {name!r} not in BUILTIN_TOOL_NAMES (drift)"


def test_user_functions_are_appended_after_builtins() -> None:
    # First-registration-wins in ToolRegistry must always keep a built-in over a
    # same-named user function, so functions are assembled last regardless of row order.
    tools = bt.build_agent_tools(
        AsyncMock(),
        agent=_agent(),
        tools=[_function("aaa_user"), *_singletons()],
        deps=_deps(),
    )
    names = [t.name for t in tools]
    assert names[-1] == "aaa_user"
    assert "web_search" in names[:-1]


def test_only_enabled_singletons_yield_tools() -> None:
    tools = bt.build_agent_tools(AsyncMock(), agent=_agent(), tools=_singletons(), deps=_deps())
    assert {t.name for t in tools} == {"web_search", "code_exec", "file"}


def test_disabled_singletons_are_skipped() -> None:
    # Only web_search enabled; code_exec/file/file_search off.
    tools = bt.build_agent_tools(
        AsyncMock(),
        agent=_agent(),
        tools=_singletons(web_search=True, code_exec=False, file=False),
        deps=_deps(),
    )
    names = {t.name for t in tools}
    assert names == {"web_search"}
    assert not any(n.startswith("mcp__") for n in names)


def test_file_search_appears_when_enabled() -> None:
    tools = bt.build_agent_tools(
        AsyncMock(),
        agent=_agent(),
        tools=_singletons(web_search=False, code_exec=False, file=False, file_search=True),
        deps=_deps(),
    )
    assert {t.name for t in tools} == {"file_search"}


def test_singletons_coexist_with_mcp_server() -> None:
    agent = _agent()
    tools = bt.build_agent_tools(
        AsyncMock(),
        agent=agent,
        tools=[
            *_singletons(web_search=True, code_exec=False, file=False),
            _mcp(("alpha",)),
        ],
        deps=_deps(),
    )
    names = {t.name for t in tools}
    assert "web_search" in names
    assert "code_exec" not in names
    assert "file" not in names
    assert any(n.endswith("__alpha") for n in names)


def test_disabled_mcp_tool_is_skipped() -> None:
    disabled_mcp = _tool(
        AgentToolType.HOSTED_MCP,
        enabled=False,
        config={"source": "package", "reference": "npx:@scope/srv", "allowed_tools": ["alpha"]},
    )
    tools = bt.build_agent_tools(AsyncMock(), agent=_agent(), tools=[disabled_mcp], deps=_deps())
    assert tools == []


def test_local_shell_is_skipped() -> None:
    tools = bt.build_agent_tools(
        AsyncMock(), agent=_agent(), tools=[_tool(AgentToolType.LOCAL_SHELL)], deps=_deps()
    )
    assert tools == []


def test_function_tool_appears() -> None:
    tools = bt.build_agent_tools(
        AsyncMock(), agent=_agent(), tools=[_function("lookup_order")], deps=_deps()
    )
    assert {t.name for t in tools} == {"lookup_order"}


# --------------------------------------------------------------------------- #
# code_exec / file                                                            #
# --------------------------------------------------------------------------- #


async def test_code_exec_maps_ok_and_error() -> None:
    runner = AsyncMock()
    runner.run_code_exec.return_value = _ok("42")
    tools = {
        t.name: t
        for t in bt.build_agent_tools(
            AsyncMock(),
            agent=_agent(),
            tools=[_tool(AgentToolType.HOSTED_CODE_INTERPRETER)],
            deps=_deps(runner=runner),
        )
    }

    res = await tools["code_exec"].invoke({"source": "print(42)"})
    assert res.content == "42"
    assert res.is_error is False

    runner.run_code_exec.return_value = _ok("", ok=False, stderr="boom")
    res2 = await tools["code_exec"].invoke({"source": "x"})
    assert res2.is_error is True
    assert "boom" in res2.content


async def test_code_exec_threads_chatroom_and_collects_artifacts() -> None:
    runner = AsyncMock()
    art = {"filename": "chart.png", "mime": "image/png", "size_bytes": 3, "rel_path": "/w/chart.png", "b64": "AAA"}
    runner.run_code_exec.return_value = ToolCallResult(
        ok=True, stdout="done", stderr="", exit_code=0, duration_ms=1, metadata={"artifacts": [art]}
    )
    sink: list[dict] = []
    room = uuid.uuid4()
    tools = {
        t.name: t
        for t in bt.build_agent_tools(
            AsyncMock(),
            agent=_agent(),
            tools=[_tool(AgentToolType.HOSTED_CODE_INTERPRETER)],
            deps=_deps(runner=runner),
            chatroom_id=room,
            artifact_sink=sink,
        )
    }
    res = await tools["code_exec"].invoke({"source": "print('x')"})
    assert res.is_error is False
    # The kernel's artifacts are accumulated for the reply, and the room id is
    # threaded so code_exec runs against the session kernel.
    assert sink == [art]
    assert runner.run_code_exec.await_args.kwargs["chatroom_id"] == room


async def test_code_exec_surfaces_kernel_restart_from_metadata() -> None:
    runner = AsyncMock()
    runner.run_code_exec.return_value = ToolCallResult(
        ok=True, stdout="hi", stderr="", exit_code=0, duration_ms=1, metadata={"restarted": True}
    )
    tools = {
        t.name: t
        for t in bt.build_agent_tools(
            AsyncMock(),
            agent=_agent(),
            tools=[_tool(AgentToolType.HOSTED_CODE_INTERPRETER)],
            deps=_deps(runner=runner),
        )
    }
    res = await tools["code_exec"].invoke({"source": "print('hi')"})
    assert res.content.startswith("[kernel restarted")
    assert "hi" in res.content


async def test_code_exec_requires_source() -> None:
    tools = {
        t.name: t
        for t in bt.build_agent_tools(
            AsyncMock(),
            agent=_agent(),
            tools=[_tool(AgentToolType.HOSTED_CODE_INTERPRETER)],
            deps=_deps(),
        )
    }
    res = await tools["code_exec"].invoke({})
    assert res.is_error is True


async def test_file_dispatches_op() -> None:
    runner = AsyncMock()
    runner.run_file_op.return_value = _ok("a\nb")
    tools = {
        t.name: t
        for t in bt.build_agent_tools(
            AsyncMock(),
            agent=_agent(),
            tools=[_tool(AgentToolType.HOSTED_FILE_WORKSPACE)],
            deps=_deps(runner=runner),
        )
    }

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
    agent = _agent()
    tools = {
        t.name: t
        for t in bt.build_agent_tools(
            AsyncMock(), agent=agent, tools=[_mcp(("alpha",))], deps=_deps(runner=runner)
        )
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
    tools = {
        t.name: t
        for t in bt.build_agent_tools(
            AsyncMock(), agent=_agent(), tools=[_mcp(("alpha",))], deps=_deps(runner=runner)
        )
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

    monkeypatch.setattr("contexts.agents.application.tools.web_search.WebSearchTool.search", _fake_search)
    tools = {
        t.name: t
        for t in bt.build_agent_tools(
            AsyncMock(),
            agent=_agent(),
            tools=[_tool(AgentToolType.HOSTED_WEB_SEARCH)],
            deps=_deps(),
        )
    }
    res = await tools["web_search"].invoke({"query": "hi"})
    assert res.is_error is False
    payload = json.loads(res.content)
    assert payload[0]["url"] == "https://x"


async def test_web_search_degrades_on_missing_key() -> None:
    # Real WebSearchTool.search with no active key raises → tool returns is_error.
    tools = {
        t.name: t
        for t in bt.build_agent_tools(
            AsyncMock(),
            agent=_agent(),
            tools=[_tool(AgentToolType.HOSTED_WEB_SEARCH)],
            deps=_deps(),
        )
    }
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
        sandbox=SimpleNamespace(
            mcp_image="smap/mcp-runtime@sha256:abc",
            code_exec_image="smap/code-exec@sha256:def",
            supervisor_url="http://mcp-sandbox-supervisor:9090",
        ),
        egress=SimpleNamespace(proxy_url="http://egress-proxy:8080", shared_secret="0a0b"),
    )
    monkeypatch.setattr("app.config.settings.get_settings", lambda: fake)
    sandbox = dr.docker_runsc_sandbox_from_settings()
    assert sandbox.mcp_image == "smap/mcp-runtime@sha256:abc"
    assert sandbox.code_exec_image == "smap/code-exec@sha256:def"
    assert sandbox.egress_shared_secret == bytes.fromhex("0a0b")
    assert sandbox.supervisor_url == "http://mcp-sandbox-supervisor:9090"
