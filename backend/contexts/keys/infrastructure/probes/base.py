"""Shared probe result + HTTP helper."""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

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


def probe_url(provider_field: str, path: str) -> str:
    """Resolve a probe endpoint from `settings.provider_probe`.

    Only the host is configurable — the path, method, headers and success
    criteria stay hard-coded in each adapter, and the probe always runs. The
    default is the real provider, and `_check_prod_secrets` refuses to boot a
    prod/staging process with an override active.

    Imported lazily: `app.config` imports well above this layer, and probes are
    constructed during request handling, not at module import.
    """
    from app.config.settings import get_settings

    base: str = getattr(get_settings().provider_probe, provider_field)
    return f"{base.rstrip('/')}{path}"


# Provider-supplied identifiers we are willing to echo. Anything outside this
# shape is dropped rather than truncated: these fields are provider-controlled
# text, and the whole point of this function is that provider text is not
# trusted. Real values (`invalid_request_error`, `model_not_found`,
# `reasoning_effort`) are all identifier-shaped; a masked key reflection or a
# sentence is not.
_SAFE_IDENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def safe_ident(value: object) -> str | None:
    """A provider-supplied field, or None if it is not identifier-shaped.

    Public because the adapters' in-stream error path needs the same filter:
    an error delivered inside an HTTP 200 is no more trustworthy than one
    delivered with a status code. See ``adapters.base.scrub_stream_error``.
    """
    return value if isinstance(value, str) and _SAFE_IDENT_RE.match(value) else None


def summarise_http_failure(response: httpx.Response) -> str:
    """Build a `test_error` string without echoing the secret.

    The provider's own `error.message` often contains an "sk-ant-..." masked
    reflection of the key, so it is never included. What is included is the
    status code plus the provider's identifier-shaped `type`, `code` and
    `param` fields, which is what actually distinguishes "this model does not
    exist" from "this parameter is not supported on this model" -- the two
    failures that otherwise arrive as an indistinguishable HTTP 400.

    `type` alone is not enough: OpenAI answers both of the above with
    `invalid_request_error` and puts the discriminating information in `code`
    and `param`.
    """
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return f"HTTP {response.status_code}"
    parts = [
        f"{label}={value}"
        for label, value in (
            ("type", safe_ident(err.get("type"))),
            ("code", safe_ident(err.get("code"))),
            ("param", safe_ident(err.get("param"))),
        )
        if value is not None
    ]
    if not parts:
        return f"HTTP {response.status_code}"
    return f"HTTP {response.status_code} ({'; '.join(parts)})"


def _is_private_ip(addr: str) -> bool:
    """True if the address is loopback, private, link-local, or reserved."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def _allow_http() -> bool:
    import os

    return os.environ.get("SMAP_ALLOW_HTTP_PROVIDERS", "").lower() in ("1", "true", "yes")


def validate_base_url(url: str) -> str:
    """Validate and normalise a user-supplied base URL for SSRF safety.

    Rejects private/loopback/link-local IPs and non-HTTPS schemes (unless
    ``SMAP_ALLOW_HTTP_PROVIDERS`` is set for local dev). Returns the
    normalised URL (trailing slash stripped).

    Raises ``ValueError`` with a user-facing message on failure.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme == "https" or (scheme == "http" and _allow_http()):
        pass
    else:
        raise ValueError(
            "base_url must use HTTPS" if not _allow_http() else "base_url must use HTTP or HTTPS"
        )

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("base_url has no hostname")

    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        raise ValueError(f"cannot resolve hostname {hostname!r}") from None

    for _family, _, _, _, sockaddr in infos:
        addr = sockaddr[0]
        if _is_private_ip(addr):
            raise ValueError(f"base_url resolves to a private address ({addr})")

    return url.rstrip("/")


__all__ = [
    "ProbeResult",
    "ProbeStatus",
    "new_http_client",
    "probe_url",
    "safe_ident",
    "summarise_http_failure",
    "validate_base_url",
]
