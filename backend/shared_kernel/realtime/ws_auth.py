"""WebSocket handshake authentication (§22.14, FE-7).

Browsers cannot set `Authorization` on a WebSocket upgrade request — the
only place a credential can hitch a ride is `Sec-WebSocket-Protocol`, and
that header is routinely recorded by reverse proxies and access logs. A raw
JWT placed there therefore leaks into infrastructure logs.

So the handshake carries a **single-use ticket**, not the JWT:

  - The client first calls `POST /api/auth/ws-ticket` over HTTPS (where the
    JWT sits in the redacted `Authorization` header). `mint_ws_ticket`
    stashes the access token in Redis behind a random opaque ticket id.
  - The client offers `ticket.<id>` as the subprotocol. `authenticate_subprotocol`
    redeems it with an atomic `GETDEL` — a replayed ticket fails — and runs
    the full JWT verification on the token it recovers.
  - A ticket later found in a log is already consumed and TTL-expired.

The server MUST echo the accepted subprotocol back, so the exact `ticket.<id>`
value is returned to Starlette on accept (harmless — the ticket is dead by
then).

In-socket refresh: clients periodically send `{"type":"refresh",
"access_token":"..."}` before their current access JWT expires. The JWT in a
frame *body* is not logged the way a handshake header is. The handler
re-verifies via `refresh_principal`; on failure the connection is closed with
code 4401 so the client can re-handshake.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import WebSocket

from contexts.identity.interfaces.facade import IdentityFacade
from shared_kernel.auth import jwt, tokens
from shared_kernel.auth.clients import get_redis
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import get_sessionmaker


class WsAuthError(Exception):
    """Raised on any handshake failure — caller closes with code 4401."""


_SUBPROTO_PREFIX = "ticket."
_WS_TICKET_PREFIX = "ws:ticket:"
_WS_TICKET_TTL_SECONDS = 30
_WS_TICKET_BYTES = 32


@dataclass(frozen=True, slots=True)
class WsAuth:
    principal: Principal
    subprotocol: str  # echoed on accept
    access_token: str


async def mint_ws_ticket(access_token: str) -> tuple[str, int]:
    """Stash `access_token` behind a fresh single-use ticket.

    Returns `(ticket, ttl_seconds)`. Called by `POST /api/auth/ws-ticket`;
    redeemed once by `authenticate_subprotocol`.
    """
    ticket = secrets.token_urlsafe(_WS_TICKET_BYTES)
    await get_redis().set(
        _WS_TICKET_PREFIX + ticket,
        access_token,
        ex=_WS_TICKET_TTL_SECONDS,
    )
    return ticket, _WS_TICKET_TTL_SECONDS


def _extract_ticket(ws: WebSocket) -> tuple[str, str]:
    """Pick the first `ticket.<id>` subprotocol from the request."""
    header = ws.headers.get("sec-websocket-protocol", "")
    if not header:
        raise WsAuthError("missing Sec-WebSocket-Protocol")
    for proto in (p.strip() for p in header.split(",")):
        if proto.startswith(_SUBPROTO_PREFIX):
            ticket = proto[len(_SUBPROTO_PREFIX) :]
            if not ticket:
                raise WsAuthError("empty ticket in subprotocol")
            return proto, ticket
    raise WsAuthError("no ticket.<id> subprotocol offered")


async def authenticate_subprotocol(ws: WebSocket) -> WsAuth:
    """Verify the connection via the single-use ticket in `Sec-WebSocket-Protocol`."""
    proto, ticket = _extract_ticket(ws)
    # GETDEL redeems the ticket atomically — a replay fails even inside the TTL.
    token = await get_redis().getdel(_WS_TICKET_PREFIX + ticket)
    if not token:
        raise WsAuthError("unknown or expired ws ticket")
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


__all__ = [
    "WsAuth",
    "WsAuthError",
    "authenticate_subprotocol",
    "mint_ws_ticket",
    "refresh_principal",
]
