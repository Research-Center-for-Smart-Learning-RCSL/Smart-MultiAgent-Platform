"""Egress Proxy FastAPI app (R12.04).

Wire shape:

- Sandbox containers use this process as their **only** network exit.
- Every inbound request carries ``x-smap-project-id`` + ``x-smap-egress-hmac``
  (the sandbox wrapper signs the project id with a shared secret). The proxy
  rejects anything else.
- Forward semantics: the request URL's host is resolved, the resolved IPs
  are screened by :func:`services.egress_proxy.ip_policy.is_blocked_ip`, and
  the host is checked against the project's ``mcp_egress_allowlist``.
- **Authorization stripping** — we drop any inbound ``authorization`` header
  so the sandbox cannot impersonate platform keys (R12.04).
- Upstream response is streamed back 1:1.
- Every call is logged with truncated request + response bodies.

Infrastructure imports (``httpx``, SQLAlchemy session, redis) are lazy so
unit tests that only exercise the IP policy / middleware can import this
module without pulling the whole stack.
"""

from __future__ import annotations

import hmac
import logging
import socket
import uuid
from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import Response

from services.egress_proxy.ip_policy import is_blocked_ip

_log = logging.getLogger("smap.egress_proxy")

_MAX_BODY_LOG_BYTES = 2048
_PROJECT_ID_HEADER = "x-smap-project-id"
_HMAC_HEADER = "x-smap-egress-hmac"
_STRIPPED_INBOUND_HEADERS: frozenset[str] = frozenset({
    "authorization",
    "proxy-authorization",
    "cookie",
    _HMAC_HEADER,
    _PROJECT_ID_HEADER,
})


@dataclass(frozen=True, slots=True)
class EgressProxySettings:
    shared_secret: bytes
    allowlist_checker: "AllowlistChecker"
    upstream_timeout_s: float = 20.0


class AllowlistChecker:
    """Indirection so the proxy can consult either the live DB or a fake.

    Production wires a small helper that opens a short-lived SQLA session
    against the main DB and calls :class:`EgressAllowlistRepository.is_allowed`.
    """

    async def is_allowed(
        self, *, project_id: uuid.UUID, hostname: str
    ) -> bool:  # pragma: no cover - overridden in composition root
        raise RuntimeError("allowlist_checker must be provided")


def _truncate(raw: bytes) -> str:
    if not raw:
        return ""
    trimmed = raw[:_MAX_BODY_LOG_BYTES]
    return trimmed.decode("utf-8", errors="replace")


def _verify_hmac(*, secret: bytes, project_id: str, signature: str) -> bool:
    expected = hmac.new(
        secret, project_id.encode("ascii"), sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _resolve_ips(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    out: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr and isinstance(sockaddr[0], str):
            out.append(sockaddr[0])
    return out


def _problem(status_code: int, slug: str, detail: str) -> Response:
    body = {
        "type": f"urn:smap:error:{slug}",
        "title": slug,
        "status": status_code,
        "detail": detail,
    }
    import json as _json  # noqa: PLC0415

    return Response(
        content=_json.dumps(body),
        status_code=status_code,
        media_type="application/problem+json",
    )


def create_app(settings: EgressProxySettings) -> FastAPI:
    app = FastAPI(title="SMAP Egress Proxy", docs_url=None, redoc_url=None)

    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def forward(full_path: str, request: Request) -> Response:  # noqa: ARG001
        # 1. Authenticate the inbound sandbox request.
        raw_pid = request.headers.get(_PROJECT_ID_HEADER, "")
        signature = request.headers.get(_HMAC_HEADER, "")
        if not raw_pid or not signature:
            return _problem(401, "mcp-egress-denied", "missing project id / hmac")
        if not _verify_hmac(
            secret=settings.shared_secret,
            project_id=raw_pid, signature=signature,
        ):
            return _problem(401, "mcp-egress-denied", "hmac mismatch")
        try:
            project_id = uuid.UUID(raw_pid)
        except ValueError:
            return _problem(400, "mcp-egress-denied", "invalid project id")

        # 2. Parse target URL. The sandbox sets the URL as the absolute URI.
        target = str(request.url)
        parts = urlsplit(target)
        # The request arrives with the proxy's own host/scheme; the actual
        # target is conveyed via a dedicated header to avoid ambiguity.
        forward_url = request.headers.get("x-smap-egress-url", "")
        if not forward_url:
            return _problem(400, "mcp-egress-denied", "missing x-smap-egress-url")
        fwd_parts = urlsplit(forward_url)
        host = fwd_parts.hostname or ""
        if not host:
            return _problem(400, "mcp-egress-denied", "invalid egress url")

        # 3. IP policy — resolve and block private / metadata / loopback.
        ips = _resolve_ips(host)
        if not ips:
            return _problem(502, "mcp-egress-denied", f"dns failure for {host}")
        if any(is_blocked_ip(ip) for ip in ips):
            _log.warning(
                "egress_blocked project=%s host=%s ips=%s",
                project_id, host, ips,
            )
            return _problem(
                403, "mcp-egress-denied",
                f"blocked host {host} resolved to disallowed IP",
            )

        # 4. Allowlist check (R12.02).
        allowed = await settings.allowlist_checker.is_allowed(
            project_id=project_id, hostname=host.lower(),
        )
        if not allowed:
            _log.warning(
                "egress_blocked_allowlist project=%s host=%s",
                project_id, host,
            )
            return _problem(
                403, "mcp-egress-denied",
                f"host {host} not on project allowlist",
            )

        # 5. Strip dangerous inbound headers, especially Authorization.
        upstream_headers: dict[str, str] = {}
        for name, value in request.headers.items():
            if name.lower() in _STRIPPED_INBOUND_HEADERS:
                continue
            if name.lower() == "host":
                continue
            upstream_headers[name] = value

        # 6. Forward via httpx.
        import httpx  # noqa: PLC0415

        body = await request.body()
        try:
            async with httpx.AsyncClient(
                timeout=settings.upstream_timeout_s, follow_redirects=False,
            ) as client:
                upstream = await client.request(
                    method=request.method,
                    url=forward_url,
                    headers=upstream_headers,
                    content=body,
                )
        except httpx.HTTPError as exc:
            return _problem(502, "mcp-egress-denied", f"upstream error: {exc}")

        _log.info(
            "egress_forward project=%s host=%s method=%s path=%s status=%s "
            "req_body=%r resp_body=%r",
            project_id,
            host,
            request.method,
            parts.path,
            upstream.status_code,
            _truncate(body),
            _truncate(upstream.content),
        )

        # 7. Relay response headers — drop hop-by-hop per RFC 7230.
        hop_by_hop = {
            "connection", "keep-alive", "proxy-authenticate",
            "proxy-authorization", "te", "trailer",
            "transfer-encoding", "upgrade",
        }
        response_headers = {
            k: v for k, v in upstream.headers.items()
            if k.lower() not in hop_by_hop
        }
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=upstream.headers.get("content-type"),
        )

    return app


__all__ = ["AllowlistChecker", "EgressProxySettings", "create_app"]
