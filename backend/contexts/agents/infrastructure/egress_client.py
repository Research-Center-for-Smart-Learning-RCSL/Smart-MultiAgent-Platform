"""Concrete :class:`EgressProxyClient` (R12.04 / SEC-H5).

Before this, the only ``EgressProxyClient`` was a Protocol plus test fakes —
so the built-in ``web_search`` tool either could not run in production or
would have called the provider directly, bypassing the proxy's allowlist /
IP-policy / HMAC entirely. This httpx-backed client signs the per-project HMAC
and forwards the absolute target URL via ``x-smap-egress-url`` so all built-in
tool egress traverses the proxy.

(With SEC-C1's internal sandbox network, *sandbox* egress is forced through
the proxy regardless of cooperation; this client closes the **backend-side**
built-in-tool path, where the call originates in-process rather than in a
sandbox.)

The HMAC construction mirrors ``services.egress_proxy.app._verify_hmac``
exactly: HMAC-SHA256 of the ascii project id under the shared secret.
"""

from __future__ import annotations

import hmac
import json as _json
import uuid
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from contexts.agents.domain.errors import McpEgressDenied

_PROJECT_ID_HEADER = "x-smap-project-id"
_HMAC_HEADER = "x-smap-egress-hmac"
_EGRESS_URL_HEADER = "x-smap-egress-url"
_PROBLEM_DENIED_TYPE = "mcp-egress-denied"
_STRIP_REQUEST_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie"})


@dataclass(frozen=True, slots=True)
class HttpxEgressProxyClient:
    """Production :class:`contexts.agents.application.mcp_ports.EgressProxyClient`.

    Structurally satisfies the protocol (duck-typed, like the test fakes) —
    not declared as a subclass to avoid a Protocol/dataclass metaclass clash.
    """

    proxy_base_url: str
    shared_secret: bytes

    def _sign(self, project_id: uuid.UUID) -> str:
        return hmac.new(self.shared_secret, str(project_id).encode("ascii"), sha256).hexdigest()

    async def request(
        self,
        *,
        method: str,
        url: str,
        project_id: uuid.UUID,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        timeout_s: float = 20.0,
    ) -> tuple[int, dict[str, str], bytes]:
        if not self.shared_secret:
            # Fail closed: without the shared secret we cannot authenticate to
            # the proxy, and we must never fall back to a direct (un-proxied)
            # call. Surfaces as the same domain error the proxy itself returns.
            raise McpEgressDenied("egress proxy shared secret is not configured")

        import httpx

        out_headers: dict[str, str] = {
            k: v for k, v in (headers or {}).items() if k.lower() not in _STRIP_REQUEST_HEADERS
        }
        out_headers[_PROJECT_ID_HEADER] = str(project_id)
        out_headers[_HMAC_HEADER] = self._sign(project_id)
        out_headers[_EGRESS_URL_HEADER] = url

        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.request(
                    method.upper(),
                    self.proxy_base_url,
                    headers=out_headers,
                    params=params,
                    json=json_body,
                )
        except httpx.HTTPError as exc:
            raise McpEgressDenied(f"egress proxy unreachable: {exc}") from exc

        # The proxy flags its OWN denials with a problem+json body whose `type`
        # ends in `mcp-egress-denied`. An upstream 4xx/5xx is relayed 1:1 with
        # the upstream content-type, so it is passed through (the caller
        # decides) rather than masqueraded as a proxy denial.
        ctype = resp.headers.get("content-type", "")
        if resp.status_code in (401, 403, 502) and ctype.startswith("application/problem+json"):
            denied = False
            detail = ""
            try:
                body = _json.loads(resp.content.decode("utf-8"))
                denied = str(body.get("type", "")).endswith(_PROBLEM_DENIED_TYPE)
                detail = str(body.get("detail") or body.get("title") or "")
            except (UnicodeDecodeError, ValueError):
                denied = False
            if denied:
                raise McpEgressDenied(f"egress proxy denied request to {url}: {detail}")

        return resp.status_code, dict(resp.headers), resp.content


def egress_proxy_client_from_settings() -> HttpxEgressProxyClient:
    """Build the client from settings (composition-root helper).

    The agent tool-runtime constructs the built-in tools (``web_search``) with
    this so their egress traverses the proxy. The shared secret is read from
    ``EGRESS_PROXY_SHARED_SECRET`` — the same value the proxy verifies. An
    absent/malformed secret yields an empty key; the client then fails closed
    (raises :class:`McpEgressDenied`) rather than calling a provider directly.
    """
    from app.config.settings import get_settings

    cfg = get_settings().egress
    try:
        secret = bytes.fromhex(cfg.shared_secret) if cfg.shared_secret else b""
    except ValueError:
        secret = b""
    return HttpxEgressProxyClient(proxy_base_url=cfg.proxy_url, shared_secret=secret)


__all__ = ["HttpxEgressProxyClient", "egress_proxy_client_from_settings"]
