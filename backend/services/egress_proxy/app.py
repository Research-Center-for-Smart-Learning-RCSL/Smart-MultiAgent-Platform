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
    import json as _json

    return Response(
        content=_json.dumps(body),
        status_code=status_code,
        media_type="application/problem+json",
    )


def create_app(settings: EgressProxySettings) -> FastAPI:
    app = FastAPI(title="SMAP Egress Proxy", docs_url=None, redoc_url=None)

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

        # 3. IP policy — resolve and block private / metadata / loopback.
        ips = _resolve_ips(host)
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
        screened_ips = frozenset(ips)

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

        # 6. DNS-rebinding mitigation: re-resolve immediately before the forward
        #    and reject if the second resolution introduces any IP not in the
        #    screened set. Without this, an attacker controlling DNS could
        #    return a public IP at screen-time and 169.254.169.254 at connect
        #    time. The window is now sub-millisecond instead of tens of ms.
        recheck_ips = _resolve_ips(host)
        if not recheck_ips:
            return _problem(502, "mcp-egress-denied", f"dns failure for {host}")
        if not set(recheck_ips).issubset(screened_ips):
            _log.warning(
                "egress_blocked_rebind project=%s host=%s " "first=%s second=%s",
                project_id,
                host,
                list(screened_ips),
                recheck_ips,
            )
            return _problem(
                403,
                "mcp-egress-denied",
                f"dns rebinding detected for {host}",
            )
        if any(is_blocked_ip(ip) for ip in recheck_ips):
            return _problem(
                403,
                "mcp-egress-denied",
                f"blocked host {host} resolved to disallowed IP (rebind)",
            )

        # 7. Forward via httpx with a streaming response body and per-call
        #    byte cap (see EgressProxySettings.response_max_bytes).
        import httpx

        body = await request.body()
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
        try:
            async with httpx.AsyncClient(
                timeout=settings.upstream_timeout_s,
                follow_redirects=False,
            ) as client:
                req = client.build_request(
                    method=request.method,
                    url=forward_url,
                    headers=upstream_headers,
                    content=body,
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
                                    f"upstream response too large " f"({declared} > {max_bytes})",
                                )
                        except ValueError:
                            pass
                    chunks: list[bytes] = []
                    received = 0
                    truncated = False
                    # `aiter_bytes` decompresses per `Content-Encoding`; we
                    # strip the encoding header below so the sandbox sees
                    # the same plaintext bytes the original code (.content)
                    # surfaced.
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
                    # Snapshot headers as a list of (k, v) tuples so we can
                    # filter out hop-by-hop entries below before handing
                    # them to Starlette. Multi-valued headers (Set-Cookie)
                    # are preserved via Response.raw_headers below.
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

        # 8. Relay response headers — drop hop-by-hop per RFC 7230. Build
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
