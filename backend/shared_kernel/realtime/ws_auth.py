"""WebSocket handshake authentication (§22.14).

Browsers cannot set `Authorization` on a WebSocket upgrade request — the
only place a token can hitch a ride is `Sec-WebSocket-Protocol`. The
server MUST echo the accepted subprotocol back, so we pick it from the
list the client sent (`bearer.<jwt>`), verify the JWT, and on accept pass
that exact value back to Starlette.

In-socket refresh: clients periodically send `{"type":"refresh",
"access_token":"..."}` before their current access JWT expires. The
handler re-verifies via `refresh_principal`; on failure the connection is
closed with code 4401 so the client can re-handshake.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import WebSocket

from contexts.identity.interfaces.facade import IdentityFacade
from shared_kernel.auth import jwt, tokens
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import get_sessionmaker


class WsAuthError(Exception):
    """Raised on any handshake failure — caller closes with code 4401."""


_SUBPROTO_PREFIX = "bearer."


@dataclass(frozen=True, slots=True)
class WsAuth:
    principal: Principal
    subprotocol: str            # echoed on accept
    access_token: str


def _extract_token(ws: WebSocket) -> tuple[str, str]:
    """Pick the first `bearer.<token>` subprotocol from the request."""
    header = ws.headers.get("sec-websocket-protocol", "")
    if not header:
        raise WsAuthError("missing Sec-WebSocket-Protocol")
    protos = [p.strip() for p in header.split(",")]
    for proto in protos:
        if proto.startswith(_SUBPROTO_PREFIX):
            token = proto[len(_SUBPROTO_PREFIX):]
            if not token:
                raise WsAuthError("empty bearer token in subprotocol")
            return proto, token
    raise WsAuthError("no bearer.<token> subprotocol offered")


async def authenticate_subprotocol(ws: WebSocket) -> WsAuth:
    """Verify the token carried in `Sec-WebSocket-Protocol: bearer.<jwt>`."""
    proto, token = _extract_token(ws)
    try:
        claims = jwt.verify_access_token(token)
    except jwt.JwtError as exc:
        raise WsAuthError(f"invalid token: {exc}") from exc
    if await tokens.is_denied(claims.jti):
        raise WsAuthError("token revoked")

    sm = get_sessionmaker()
    async with sm() as session:
        profile = await IdentityFacade(session).get_profile(claims.sub)
        if profile is None or profile.status.value != "active":
            raise WsAuthError("account inactive")
        principal = Principal(
            user_id=profile.id,
            is_admin=profile.is_admin,
            email_verified=profile.email_verified,
        )
    return WsAuth(principal=principal, subprotocol=proto, access_token=token)


async def refresh_principal(access_token: str) -> Principal:
    """Re-verify a token presented via in-socket refresh."""
    try:
        claims = jwt.verify_access_token(access_token)
    except jwt.JwtError as exc:
        raise WsAuthError(f"refresh failed: {exc}") from exc
    if await tokens.is_denied(claims.jti):
        raise WsAuthError("token revoked")
    sm = get_sessionmaker()
    async with sm() as session:
        profile = await IdentityFacade(session).get_profile(claims.sub)
        if profile is None or profile.status.value != "active":
            raise WsAuthError("account inactive")
        return Principal(
            user_id=profile.id,
            is_admin=profile.is_admin,
            email_verified=profile.email_verified,
        )


__all__ = ["WsAuth", "WsAuthError", "authenticate_subprotocol", "refresh_principal"]
