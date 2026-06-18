"""Egress Proxy FastAPI app (R12.04).

Wire shape:

- Sandbox containers use this process as their **only** network exit.
- Every inbound request carries ``x-smap-project-id`` + ``x-smap-egress-hmac``
  (the sandbox wrapper signs the project id with a shared secret). The proxy
  rejects anything else.
- Forward semantics: the request URL's host is resolved, the resolved IPs
  are screened by :func:`services.egress_proxy.ip_policy.is_blocked_ip`, and
  the host is checked against the project's ``mcp_egress_allowlist``. The
  outbound connection is then pinned to one of the *screened* IP literals so
  ``httpx`` cannot perform its own (unscreened) DNS resolution at connect
  time — closing the DNS-rebinding / SSRF window structurally.
- **Authorization stripping** — we drop any inbound ``authorization`` header
  so the sandbox cannot impersonate platform keys (R12.04).
- Upstream response is streamed back 1:1.
- Every call is logged with truncated request + response bodies.

Infrastructure imports (``httpx``, SQLAlchemy session, redis) are lazy so
unit tests that only exercise the IP policy / middleware can import this
module without pulling the whole stack.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import socket
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import FastAPI, Request
from fastapi.responses import Response

from services.egress_proxy.ip_policy import is_blocked_ip

_log = logging.getLogger("smap.egress_proxy")

_MAX_BODY_LOG_BYTES = 2048
_PROJECT_ID_HEADER = "x-smap-project-id"
_HMAC_HEADER = "x-smap-egress-hmac"
_STRIPPED_INBOUND_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        _HMAC_HEADER,
        _PROJECT_ID_HEADER,
    }
)


_DEFAULT_RESPONSE_CAP_BYTES = 16 * 1024 * 1024  # 16 MiB; see EgressProxySettings.


_DEFAULT_REQUEST_CAP_BYTES = 1 * 1024 * 1024  # 1 MiB inbound body cap.


@dataclass(frozen=True, slots=True)
class EgressProxySettings:
    shared_secret: bytes
    allowlist_checker: AllowlistChecker
    upstream_timeout_s: float = 20.0
    # Hard cap on bytes streamed back per upstream response. Sandboxed agents
    # otherwise OOM the proxy by hitting an unbounded endpoint. 16 MiB is high
    # enough for most JSON / HTML / model-doc payloads; larger transfers should
    # not flow through the egress proxy.
    response_max_bytes: int = _DEFAULT_RESPONSE_CAP_BYTES
    request_max_bytes: int = _DEFAULT_REQUEST_CAP_BYTES


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
        secret,
        project_id.encode("ascii"),
        sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _resolve_ips_sync(host: str) -> list[str]:
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


async def _resolve_ips(host: str) -> list[str]:
    return await asyncio.to_thread(_resolve_ips_sync, host)


_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}


def _host_header(host: str, scheme: str, port: int | None) -> str:
    """Build the ``Host`` header for the upstream — the real hostname plus a
    non-default port. IPv6 literals are bracketed."""
    name = f"[{host}]" if ":" in host else host
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        return f"{name}:{port}"
    return name


def _pin_url(*, scheme: str, path: str, query: str, ip: str, port: int | None) -> str:
    """Rewrite the forward URL so its host is a pre-screened IP literal.

    The TCP connection therefore lands on the exact address that passed
    :func:`is_blocked_ip`; ``Host`` / SNI are carried separately so the
    upstream still sees the original hostname. IPv6 literals are bracketed.
    """
    netloc = f"[{ip}]" if ":" in ip else ip
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((scheme, netloc, path or "/", query, ""))


def _problem(status_code: int, slug: str, detail: str) -> Response:
    body = {
        "type": f"urn:smap:error:{slug}",
        "title": slug,
        "status": status_code,
        "detail": detail,
    }
    import json as _json

    return Response(
        content=_json.dumps(body),
        status_code=status_code,
        media_type="application/problem+json",
    )


def create_app(settings: EgressProxySettings) -> FastAPI:

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> Any:
        import httpx

        app.state.httpx_client = httpx.AsyncClient(
            timeout=settings.upstream_timeout_s,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=30,
            ),
        )
        yield
        await app.state.httpx_client.aclose()

    app = FastAPI(
        title="SMAP Egress Proxy",
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )

    @app.get("/healthz")
    async def healthz() -> Response:
        return Response(content='{"status":"ok"}', media_type="application/json")

    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def forward(full_path: str, request: Request) -> Response:
        # 1. Authenticate the inbound sandbox request.
        raw_pid = request.headers.get(_PROJECT_ID_HEADER, "")
        signature = request.headers.get(_HMAC_HEADER, "")
        if not raw_pid or not signature:
            return _problem(401, "mcp-egress-denied", "missing project id / hmac")
        if not _verify_hmac(
            secret=settings.shared_secret,
            project_id=raw_pid,
            signature=signature,
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
        scheme = (fwd_parts.scheme or "").lower()
        if scheme not in ("http", "https"):
            return _problem(400, "mcp-egress-denied", f"unsupported egress scheme {scheme!r}")
        try:
            target_port = fwd_parts.port
        except ValueError:
            return _problem(400, "mcp-egress-denied", "invalid egress url port")

        # 3. IP policy — resolve the target host and screen every candidate
        #    address. The outbound socket is then pinned to one of these
        #    screened IP literals (step 6) instead of letting httpx re-resolve
        #    the hostname at TCP-connect time. Pinning to a pre-validated
        #    address closes the DNS-rebinding / SSRF window structurally —
        #    there is no second, unscreened resolution to race.
        ips = await _resolve_ips(host)
        if not ips:
            return _problem(502, "mcp-egress-denied", f"dns failure for {host}")
        if any(is_blocked_ip(ip) for ip in ips):
            _log.warning(
                "egress_blocked project=%s host=%s ips=%s",
                project_id,
                host,
                ips,
            )
            return _problem(
                403,
                "mcp-egress-denied",
                f"blocked host {host} resolved to disallowed IP",
            )

        # 4. Allowlist check (R12.02).
        allowed = await settings.allowlist_checker.is_allowed(
            project_id=project_id,
            hostname=host.lower(),
        )
        if not allowed:
            _log.warning(
                "egress_blocked_allowlist project=%s host=%s",
                project_id,
                host,
            )
            return _problem(
                403,
                "mcp-egress-denied",
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
        # The inbound `Host` is the proxy's own host. Because the forward
        # below connects to an IP literal, set `Host` explicitly to the real
        # target so the upstream still sees the correct virtual host.
        upstream_headers["host"] = _host_header(host, scheme, target_port)

        # 6. Forward via httpx with a streaming response body and per-call
        #    byte cap (see EgressProxySettings.response_max_bytes). We connect
        #    to a *screened IP literal* so the socket lands on the exact
        #    address `is_blocked_ip` validated — httpx performs no DNS of its
        #    own. The `Host` header (above) and the `sni_hostname` request
        #    extension preserve virtual-hosting and TLS certificate
        #    verification against the original hostname.
        connect_ip = ips[0]
        pinned_url = _pin_url(
            scheme=scheme,
            path=fwd_parts.path,
            query=fwd_parts.query,
            ip=connect_ip,
            port=target_port,
        )

        body = await request.body()
        if len(body) > settings.request_max_bytes:
            return _problem(
                413,
                "mcp-egress-denied",
                f"request body too large ({len(body)} > {settings.request_max_bytes})",
            )
        max_bytes = settings.response_max_bytes
        hop_by_hop = {
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailer",
            "transfer-encoding",
            "upgrade",
            # We always re-encode as a buffered Response, so drop the upstream
            # length and let starlette set Content-Length from the buffer.
            "content-length",
            "content-encoding",
        }
        import httpx

        client: httpx.AsyncClient = request.app.state.httpx_client
        try:
            req = client.build_request(
                method=request.method,
                url=pinned_url,
                headers=upstream_headers,
                content=body,
                extensions={"sni_hostname": host},
            )
            upstream = await client.send(req, stream=True)
            try:
                declared = upstream.headers.get("content-length")
                if declared is not None:
                    try:
                        if int(declared) > max_bytes:
                            return _problem(
                                502,
                                "mcp-egress-denied",
                                f"upstream response too large ({declared} > {max_bytes})",
                            )
                    except ValueError:
                        pass
                chunks: list[bytes] = []
                received = 0
                truncated = False
                async for chunk in upstream.aiter_bytes():
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > max_bytes:
                        truncated = True
                        break
                    chunks.append(chunk)
                if truncated:
                    return _problem(
                        502,
                        "mcp-egress-denied",
                        f"upstream response exceeded cap of {max_bytes} bytes",
                    )
                upstream_body = b"".join(chunks)
                upstream_status = upstream.status_code
                upstream_multi_headers: list[tuple[str, str]] = list(upstream.headers.multi_items())
                upstream_content_type = upstream.headers.get("content-type")
            finally:
                await upstream.aclose()
        except httpx.HTTPError as exc:
            return _problem(502, "mcp-egress-denied", f"upstream error: {exc}")

        _log.info(
            "egress_forward project=%s host=%s method=%s path=%s status=%s " "req_body=%r resp_body=%r",
            project_id,
            host,
            request.method,
            parts.path,
            upstream_status,
            _truncate(body),
            _truncate(upstream_body),
        )

        # 7. Relay response headers — drop hop-by-hop per RFC 7230. Build
        #    a Response and then overwrite raw_headers so multi-valued
        #    entries (Set-Cookie) round-trip without collapsing.
        response = Response(
            content=upstream_body,
            status_code=upstream_status,
            media_type=upstream_content_type,
        )
        relayed: list[tuple[bytes, bytes]] = []
        for k, v in upstream_multi_headers:
            kl = k.lower()
            if kl in hop_by_hop:
                continue
            # Skip content-type / content-length: Response already wrote
            # them based on the body buffer + media_type.
            if kl in ("content-type", "content-length"):
                continue
            relayed.append((kl.encode("latin-1"), v.encode("latin-1")))
        # Preserve the framing/length headers Response already computed.
        for raw_k, raw_v in response.raw_headers:
            if raw_k.lower() in (b"content-type", b"content-length"):
                relayed.append((raw_k, raw_v))
        response.raw_headers = relayed
        return response

    return app


__all__ = ["AllowlistChecker", "EgressProxySettings", "create_app"]
