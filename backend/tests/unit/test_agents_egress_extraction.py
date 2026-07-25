"""perform_egress_request extraction + AgentsFacade validator seam (AC-12, §5.2).

The allowlist + fixed-window rate-limit + proxy-call policy was extracted from
the function-tool ``_invoke`` closure into ``perform_egress_request`` so both the
agent-tool path and the activities validator path share one copy. These tests pin
that shared policy and the facade's neutral-result mapping.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.agents.application.egress import (
    EgressBlocked,
    EgressOutcome,
    perform_egress_request,
)
from contexts.agents.domain.errors import McpEgressDenied
from contexts.agents.interfaces.facade import AgentsFacade, EgressResponse, McpInvokeResult

_ALLOW = "contexts.agents.application.runtime.builtin_tools.function_egress_allowed"
_REDIS = "shared_kernel.auth.clients.get_redis"


class _FakeProxy:
    def __init__(self, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._status = status
        self._headers = headers or {}

    async def request(self, **kwargs: Any) -> tuple[int, dict[str, str], bytes]:
        self.calls.append(kwargs)
        return self._status, self._headers, b"ok"


def _redis_returning(count: int) -> MagicMock:
    pipe = MagicMock()
    pipe.incr.return_value = None
    pipe.expire.return_value = None
    pipe.execute = AsyncMock(return_value=[count, True])
    redis = MagicMock()
    redis.pipeline.return_value = pipe
    return redis


class TestPerformEgressRequest:
    async def test_get_uses_query_params_on_success(self) -> None:
        proxy = _FakeProxy()
        with (
            patch(_ALLOW, new=AsyncMock(return_value=("api.example.com", True))),
            patch(_REDIS, return_value=_redis_returning(1)),
        ):
            outcome = await perform_egress_request(
                MagicMock(),
                project_id=uuid.uuid4(),
                method="get",
                url="https://api.example.com/x",
                proxy=proxy,  # type: ignore[arg-type]
                args={"q": "1"},
            )
        assert isinstance(outcome, EgressOutcome)
        assert outcome.status == 200
        assert outcome.ok
        assert proxy.calls[0]["params"] == {"q": "1"}
        assert "json_body" not in proxy.calls[0]

    async def test_post_uses_json_body(self) -> None:
        proxy = _FakeProxy()
        with (
            patch(_ALLOW, new=AsyncMock(return_value=("api.example.com", True))),
            patch(_REDIS, return_value=_redis_returning(1)),
        ):
            await perform_egress_request(
                MagicMock(),
                project_id=uuid.uuid4(),
                method="POST",
                url="https://api.example.com/x",
                proxy=proxy,  # type: ignore[arg-type]
                args={"a": 1},
            )
        assert proxy.calls[0]["json_body"] == {"a": 1}

    async def test_allowlist_miss_raises_blocked(self) -> None:
        with (
            patch(_ALLOW, new=AsyncMock(return_value=("evil.example.com", False))),
            pytest.raises(EgressBlocked) as ei,
        ):
            await perform_egress_request(
                MagicMock(),
                project_id=uuid.uuid4(),
                method="GET",
                url="https://evil.example.com",
                proxy=_FakeProxy(),  # type: ignore[arg-type]
            )
        assert ei.value.kind == "allowlist"
        assert ei.value.host == "evil.example.com"

    async def test_rate_limit_trips(self) -> None:
        with (
            patch(_ALLOW, new=AsyncMock(return_value=("api.example.com", True))),
            patch(_REDIS, return_value=_redis_returning(61)),
            pytest.raises(EgressBlocked) as ei,
        ):
            await perform_egress_request(
                MagicMock(),
                project_id=uuid.uuid4(),
                method="GET",
                url="https://api.example.com",
                proxy=_FakeProxy(),  # type: ignore[arg-type]
            )
        assert ei.value.kind == "rate_limit"

    async def test_rate_limiter_offline_fails_closed(self) -> None:
        with (
            patch(_ALLOW, new=AsyncMock(return_value=("api.example.com", True))),
            patch(_REDIS, side_effect=RuntimeError("redis down")),
            pytest.raises(EgressBlocked) as ei,
        ):
            await perform_egress_request(
                MagicMock(),
                project_id=uuid.uuid4(),
                method="GET",
                url="https://api.example.com",
                proxy=_FakeProxy(),  # type: ignore[arg-type]
            )
        assert ei.value.kind == "rate_limiter_offline"

    async def test_outcome_carries_response_headers(self) -> None:
        proxy = _FakeProxy(status=301, headers={"location": "https://api.example.com/y/"})
        with (
            patch(_ALLOW, new=AsyncMock(return_value=("api.example.com", True))),
            patch(_REDIS, return_value=_redis_returning(1)),
        ):
            outcome = await perform_egress_request(
                MagicMock(),
                project_id=uuid.uuid4(),
                method="GET",
                url="https://api.example.com/x",
                proxy=proxy,  # type: ignore[arg-type]
            )
        assert outcome.location == "https://api.example.com/y/"

    async def test_3xx_is_not_ok(self) -> None:
        assert EgressOutcome(status=301, body=b"").ok is False

    async def test_header_lookup_is_case_insensitive(self) -> None:
        outcome = EgressOutcome(status=301, body=b"", headers=(("Location", "https://x/y/"),))
        assert outcome.header("location") == "https://x/y/"

    async def test_2xx_still_ok(self) -> None:
        assert EgressOutcome(status=200, body=b"").ok is True
        assert EgressOutcome(status=204, body=b"").ok is True

    async def test_4xx_5xx_not_ok(self) -> None:
        assert EgressOutcome(status=404, body=b"").ok is False
        assert EgressOutcome(status=500, body=b"").ok is False


class TestFacadeEgressSeam:
    async def test_egress_request_maps_location(self) -> None:
        deps = MagicMock()
        outcome = EgressOutcome(
            status=301, body=b"", headers=(("Location", "https://validator.example.com/score/"),)
        )
        with (
            patch(
                "contexts.agents.application.runtime.builtin_tools.default_builtin_deps",
                return_value=deps,
            ),
            patch(
                "contexts.agents.application.egress.perform_egress_request",
                new=AsyncMock(return_value=outcome),
            ),
        ):
            resp = await AgentsFacade(MagicMock()).egress_request(
                project_id=uuid.uuid4(), method="POST", url="https://validator.example.com/score"
            )
        assert resp.status == 301
        assert resp.location == "https://validator.example.com/score/"

    async def test_egress_request_maps_success(self) -> None:
        deps = MagicMock()
        with (
            patch(
                "contexts.agents.application.runtime.builtin_tools.default_builtin_deps",
                return_value=deps,
            ),
            patch(
                "contexts.agents.application.egress.perform_egress_request",
                new=AsyncMock(return_value=EgressOutcome(status=201, body=b"done")),
            ),
        ):
            resp = await AgentsFacade(MagicMock()).egress_request(
                project_id=uuid.uuid4(), method="POST", url="https://api.example.com", body={"x": 1}
            )
        assert isinstance(resp, EgressResponse)
        assert resp.status == 201
        assert resp.body == b"done"
        assert resp.blocked is None

    async def test_egress_request_maps_blocked(self) -> None:
        deps = MagicMock()
        with (
            patch(
                "contexts.agents.application.runtime.builtin_tools.default_builtin_deps",
                return_value=deps,
            ),
            patch(
                "contexts.agents.application.egress.perform_egress_request",
                new=AsyncMock(side_effect=EgressBlocked("no", kind="allowlist", host="h")),
            ),
        ):
            resp = await AgentsFacade(MagicMock()).egress_request(
                project_id=uuid.uuid4(), method="GET", url="https://x"
            )
        assert resp.status is None
        assert resp.blocked == "allowlist"

    async def test_egress_request_maps_policy_denial(self) -> None:
        deps = MagicMock()
        with (
            patch(
                "contexts.agents.application.runtime.builtin_tools.default_builtin_deps",
                return_value=deps,
            ),
            patch(
                "contexts.agents.application.egress.perform_egress_request",
                new=AsyncMock(side_effect=McpEgressDenied("denied")),
            ),
        ):
            resp = await AgentsFacade(MagicMock()).egress_request(
                project_id=uuid.uuid4(), method="GET", url="https://x"
            )
        assert resp.blocked == "policy"

    async def test_invoke_mcp_tool_maps_result(self) -> None:
        runner = MagicMock()
        runner.invoke_mcp_tool = AsyncMock(
            return_value=MagicMock(ok=True, stdout="out", stderr="", exit_code=0, duration_ms=12)
        )
        deps = MagicMock()
        deps.runner = runner
        with patch(
            "contexts.agents.application.runtime.builtin_tools.default_builtin_deps",
            return_value=deps,
        ):
            res = await AgentsFacade(MagicMock()).invoke_mcp_tool(
                project_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                binding_id=uuid.uuid4(),
                tool_name="score",
                arguments={"a": 1},
            )
        assert isinstance(res, McpInvokeResult)
        assert res.ok
        assert res.stdout == "out"
        assert res.exit_code == 0

    async def test_invoke_mcp_tool_egress_denied_is_not_raised(self) -> None:
        runner = MagicMock()
        runner.invoke_mcp_tool = AsyncMock(side_effect=McpEgressDenied("denied"))
        deps = MagicMock()
        deps.runner = runner
        with patch(
            "contexts.agents.application.runtime.builtin_tools.default_builtin_deps",
            return_value=deps,
        ):
            res = await AgentsFacade(MagicMock()).invoke_mcp_tool(
                project_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                binding_id=uuid.uuid4(),
                tool_name="score",
                arguments={},
            )
        assert res.ok is False
        assert res.exit_code == 42
