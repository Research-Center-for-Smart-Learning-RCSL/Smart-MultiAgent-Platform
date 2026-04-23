"""Shared probe result + HTTP helper."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Final

import httpx

_PROBE_TIMEOUT_SECONDS: Final = 5.0


class ProbeStatus(str, enum.Enum):
    OK = "ok"
    FAILED = "failed"
    UNTESTED = "untested"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Persisted to `api_keys.test_status` + `api_keys.test_error`.

    `error` is a short human-readable string — NEVER the raw provider body
    and NEVER anything echoing the secret back (R7.15, no-plaintext CI grep).
    """

    status: ProbeStatus
    error: str | None = None

    @classmethod
    def ok(cls) -> "ProbeResult":
        return cls(status=ProbeStatus.OK, error=None)

    @classmethod
    def failed(cls, reason: str) -> "ProbeResult":
        # Trim to a sane length so we don't persist megabytes of provider HTML.
        return cls(status=ProbeStatus.FAILED, error=reason[:500])


def new_http_client() -> httpx.AsyncClient:
    """Probe-scoped client.

    Probes run synchronously to an upload request so the 5-second timeout
    double-serves as a circuit breaker — we'd rather fail-closed than pin a
    request worker waiting on a hung provider.
    """
    return httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS)


def summarise_http_failure(response: httpx.Response) -> str:
    """Build a `test_error` string without echoing the secret.

    The provider's own error message often contains "sk-ant-..." masked
    reflections, so we keep only the status code + the first JSON `error.type`
    or `error.code` field when present.
    """
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        kind = err.get("type") or err.get("code")
        if kind:
            return f"HTTP {response.status_code} ({kind})"
    if isinstance(err, str):
        return f"HTTP {response.status_code}"
    return f"HTTP {response.status_code}"


__all__ = [
    "ProbeResult",
    "ProbeStatus",
    "new_http_client",
    "summarise_http_failure",
]
