"""Shared probe result + HTTP helper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import httpx

from contexts.keys.domain.probe_status import ProbeStatus

_PROBE_TIMEOUT_SECONDS: Final = 5.0


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Persisted to `api_keys.test_status` + `api_keys.test_error`.

    `error` is a short human-readable string — NEVER the raw provider body
    and NEVER anything echoing the secret back (R7.15, no-plaintext CI grep).
    """

    status: ProbeStatus
    error: str | None = None

    @classmethod
    def ok(cls) -> ProbeResult:
        return cls(status=ProbeStatus.OK, error=None)

    @classmethod
    def failed(cls, reason: str) -> ProbeResult:
        # Trim to a sane length so we don't persist megabytes of provider HTML.
        return cls(status=ProbeStatus.FAILED, error=reason[:500])

    def audit_category(self) -> str:
        """Coarse, secret-free classification of this probe outcome.

        The raw `error` string can still carry provider-supplied fragments —
        `summarise_http_failure` interpolates the provider's `error.type` /
        `error.code` — and `audit.redact()` only catches values that match a
        fixed set of secret *shapes*. So the audit trail records this
        closed-vocabulary category rather than `error` itself (SEC-6); the
        verbatim string stays on the `test_error` column for the key owner.
        """
        if self.status is ProbeStatus.OK:
            return "ok"
        if self.status is ProbeStatus.UNTESTED:
            return "untested"
        err = self.error or ""
        if err == "unauthorized":
            return "unauthorized"
        if err == "missing_cx":
            return "config_error"
        if err.startswith("network:"):
            return "network"
        if err.startswith("HTTP "):
            # `summarise_http_failure` emits `HTTP <int>[ (kind)]`; keep only
            # the numeric status code — never the provider-supplied `kind`.
            parts = err.split()
            if len(parts) > 1 and parts[1].isdigit():
                return f"http_{parts[1]}"
            return "http_error"
        return "unknown"


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
