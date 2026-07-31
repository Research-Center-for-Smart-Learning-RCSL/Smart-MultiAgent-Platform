"""_build_function_tool's result shaping for non-2xx egress outcomes.

A 3xx is structurally empty-bodied — ``services/egress_proxy`` never follows
redirects (SSRF policy) — so it must surface to the model as an actionable
error naming the redirect target, not a success with nothing in it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from contexts.agents.application.runtime import builtin_tools as bt
from contexts.agents.domain.models import AgentTool, AgentToolType

_NOW = datetime(2026, 6, 22, 12, 0, 0)
_ALLOW = "contexts.agents.application.runtime.builtin_tools.function_egress_allowed"
_REDIS = "shared_kernel.auth.clients.get_redis"


def _session() -> AsyncMock:
    """A stand-in turn session that supports ``begin_nested()``.

    The tool audit write is savepointed (``audit.emit(isolated=True)``), and a bare
    ``AsyncMock`` returns a coroutine there rather than an async context manager, so
    every audit write would fail — and a call whose audit row was lost is now
    reported to the model as an error.
    """
    db = AsyncMock()
    db.begin_nested = MagicMock(return_value=AsyncMock())
    db.info = {}
    return db


def _agent() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())


def _function(name: str = "lookup_order") -> AgentTool:
    return AgentTool(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        tool_type=AgentToolType.LOCAL_FUNCTION,
        enabled=True,
        display_name=None,
        config={
            "name": name,
            "description": "d",
            "parameters": {"type": "object", "properties": {}},
            "http": {"method": "GET", "url": "https://api.partner.example/orders"},
        },
        created_at=_NOW,
    )


class _FakeProxy:
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        self._status = status
        self._headers = headers or {}

    async def request(self, **_kw: Any) -> tuple[int, dict[str, str], bytes]:
        return self._status, self._headers, b""


def _deps(proxy: Any) -> bt.BuiltinToolDeps:
    return bt.BuiltinToolDeps(
        runner=AsyncMock(),
        proxy=proxy,
        adapters={},
        cache=object(),
        rate_limiter=object(),
    )  # type: ignore[arg-type]


def _redis_returning(count: int) -> Any:
    pipe = MagicMock()
    pipe.incr.return_value = None
    pipe.expire.return_value = None
    pipe.execute = AsyncMock(return_value=[count, True])
    redis = MagicMock()
    redis.pipeline.return_value = pipe
    return redis


async def _invoke_against(status: int, headers: dict[str, str] | None = None) -> Any:
    tool = _function()
    fn_tool = bt._build_function_tool(
        _session(), agent=_agent(), tool=tool, deps=_deps(_FakeProxy(status, headers))
    )
    with (
        patch(_ALLOW, new=AsyncMock(return_value=("api.partner.example", True))),
        patch(_REDIS, return_value=_redis_returning(1)),
    ):
        return await fn_tool.invoke({})


class TestFunctionToolRedirectResult:
    async def test_redirect_is_an_error_result(self) -> None:
        res = await _invoke_against(301, {"location": "https://api.partner.example/orders/"})
        assert res.is_error is True

    async def test_redirect_message_names_the_location(self) -> None:
        res = await _invoke_against(301, {"location": "https://api.partner.example/orders/"})
        assert "https://api.partner.example/orders/" in res.content

    async def test_redirect_audits_as_failure(self, monkeypatch) -> None:
        audit_calls: list[dict[str, Any]] = []

        async def _fake_audit(_db, _agent, _tool, _name, *, ok: bool) -> None:
            audit_calls.append({"ok": ok})

        monkeypatch.setattr(bt, "_audit_tool_invoke", _fake_audit)
        await _invoke_against(301, {"location": "https://api.partner.example/orders/"})
        assert audit_calls == [{"ok": False}]

    async def test_2xx_is_still_a_success(self) -> None:
        res = await _invoke_against(200)
        assert res.is_error is False
