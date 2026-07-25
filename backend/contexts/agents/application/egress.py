"""Reusable outbound-egress policy (SoC seam for the activities validator worker).

Extracted from the function-tool ``_invoke`` closure in
``runtime/builtin_tools.py`` so both the agent-tool path and the activities
webhook-validator path share ONE copy of the allowlist + fixed-window
rate-limit + egress-proxy-call + status policy (activities-platform-core §5.2).

Auth resolution and auditing stay caller-side: the agent-tool path resolves
credentials from an ``AgentTool`` row; the validator path derives them from a
validator config. Only the network policy lives here.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application.mcp_ports import EgressProxyClient

#: Default per-project fixed-window ceiling (calls/minute), matching the legacy
#: function-tool limit.
DEFAULT_EGRESS_RATE_LIMIT_PER_MINUTE = 60

BlockKind = Literal["allowlist", "rate_limit", "rate_limiter_offline"]


def _normalize_headers(headers: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """Freeze proxy response headers onto the outcome.

    ``egress_client.py`` returns ``dict(resp.headers)`` and httpx lowercases
    keys on that conversion, but the port protocol only promises
    ``dict[str, str]`` — lookup normalises case defensively rather than
    relying on that implementation detail.
    """
    return tuple((str(k), str(v)) for k, v in headers.items())


@dataclass(frozen=True, slots=True)
class EgressOutcome:
    """A completed egress round-trip."""

    status: int
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_proxy_response(cls, status: int, headers: Mapping[str, str], body: bytes) -> EgressOutcome:
        return cls(status=status, body=body, headers=_normalize_headers(headers))

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def header(self, name: str) -> str | None:
        """Case-insensitive header lookup."""
        needle = name.lower()
        for key, value in self.headers:
            if key.lower() == needle:
                return value
        return None

    @property
    def location(self) -> str | None:
        return self.header("location")


class EgressBlocked(Exception):
    """Egress was refused *before* the proxy call by local policy.

    ``kind`` distinguishes an allowlist miss, a rate-limit trip, and a
    rate-limiter outage (which fails closed) so the caller can render its own
    message and, for the validator path, classify the failure.
    """

    def __init__(self, message: str, *, kind: BlockKind, host: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.host = host


async def perform_egress_request(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    method: str,
    url: str,
    proxy: EgressProxyClient,
    args: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    upstream_auth: tuple[str, str] | None = None,
    timeout_s: float = 30.0,
    rate_limit_prefix: str = "function",
    rate_limit_per_minute: int = DEFAULT_EGRESS_RATE_LIMIT_PER_MINUTE,
) -> EgressOutcome:
    """Allowlist -> per-project fixed-window rate limit -> egress-proxy request.

    Raises :class:`EgressBlocked` for allowlist / rate-limit refusals (before any
    outbound call). ``McpEgressDenied`` (proxy-side allowlist/DNS-pin/HMAC
    failure) and any other proxy exception propagate unchanged to the caller,
    which owns audit + result shaping. ``args`` maps to query params for
    GET/DELETE and to a JSON body otherwise.
    """
    from contexts.agents.application.runtime.builtin_tools import function_egress_allowed

    host, allowed = await function_egress_allowed(db, project_id=project_id, url=url)
    if not allowed:
        raise EgressBlocked(
            f"host {host} is not on the project egress allowlist", kind="allowlist", host=host
        )

    try:
        from shared_kernel.auth.clients import get_redis

        redis = get_redis()
        window = int(time.time()) // 60
        rl_key = f"{rate_limit_prefix}:rl:{project_id}:{window}"
        pipe = redis.pipeline(transaction=False)
        pipe.incr(rl_key, 1)
        pipe.expire(rl_key, 70)
        rl_results = await pipe.execute()
        over_limit = int(rl_results[0]) > rate_limit_per_minute
    except Exception as exc:
        # Fail closed: a rate-limiter outage must not open an unbounded egress.
        raise EgressBlocked("rate-limiter offline", kind="rate_limiter_offline") from exc
    if over_limit:
        raise EgressBlocked("rate limit exceeded", kind="rate_limit")

    method_u = method.upper()
    payload = dict(args or {})
    if method_u in ("GET", "DELETE"):
        status, resp_headers, body = await proxy.request(
            method=method_u,
            url=url,
            project_id=project_id,
            headers=headers,
            params=payload,
            upstream_auth=upstream_auth,
            timeout_s=timeout_s,
        )
    else:
        status, resp_headers, body = await proxy.request(
            method=method_u,
            url=url,
            project_id=project_id,
            headers=headers,
            json_body=payload,
            upstream_auth=upstream_auth,
            timeout_s=timeout_s,
        )
    return EgressOutcome.from_proxy_response(status, resp_headers, body)


__all__ = [
    "DEFAULT_EGRESS_RATE_LIMIT_PER_MINUTE",
    "EgressBlocked",
    "EgressOutcome",
    "perform_egress_request",
]
