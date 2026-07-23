"""Google OpenID Connect adapter — Authorization Code + PKCE (R6.14).

This module owns the *protocol* mechanics of "Sign in with Google":

  * PKCE + `state` + `nonce` generation
  * building the authorization-redirect URL
  * exchanging the code for tokens (server-side, with the client secret)
  * verifying the returned `id_token` against Google's JWKS

Security invariants (see the dossier §8):
  * The authorize / token / JWKS URLs are **pinned constants** — never derived
    from the (attacker-influenceable) `iss` claim — so there is no SSRF surface.
  * `id_token` verification pins ``algorithms=["RS256"]`` (blocks alg-confusion)
    and checks `aud`, `iss`, `exp`, and `nonce`.
  * All outbound calls carry a bounded timeout so an unauthenticated callback
    can never hang on a slow Google endpoint (fail closed).

The client secret is sourced from Vault KV (`secret/smap/config/google_oauth`),
never the environment. No token, code, verifier, or secret is ever logged.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from app.config.settings import get_settings
from shared_kernel.auth.clients import get_redis, get_vault_client

# Pinned Google OIDC endpoints (never discovered from the token/iss — SSRF-nil).
_AUTHORIZE_URL: Final = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL: Final = "https://oauth2.googleapis.com/token"
_JWKS_URL: Final = "https://www.googleapis.com/oauth2/v3/certs"
_ISSUERS: Final = frozenset({"https://accounts.google.com", "accounts.google.com"})
_SCOPES: Final = "openid email profile"
_VAULT_KV_PATH: Final = "smap/config/google_oauth"
_STATE_PREFIX: Final = "oauth:state:"


class GoogleOAuthError(Exception):
    """Code-exchange or id_token verification failed (network, or an invalid /
    forged token). The application layer maps this to a fail-closed 4xx/503 and
    never leaks the underlying token material."""


class GoogleOAuthNotConfigured(GoogleOAuthError):
    """Google login is not configured (no client id, or no Vault secret)."""


class GoogleOAuthUnreachable(GoogleOAuthError):
    """Google's token/JWKS endpoint was unreachable within the timeout. Distinct
    from a verification failure so the caller can fail closed with a 503 (not a
    400) and never hang."""


@dataclass(frozen=True, slots=True)
class GoogleProfile:
    sub: str
    email: str
    email_verified: bool
    name: str | None


@dataclass(frozen=True, slots=True)
class OAuthState:
    """Server-side, single-use OAuth transaction state, keyed by `state`."""

    code_verifier: str
    nonce: str
    mode: str  # "login" | "link"
    user_id: str | None  # set only for mode="link"


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using the S256 method (RFC 7636)."""
    verifier = secrets.token_urlsafe(64)  # 86 chars, within the 43-128 range
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_nonce() -> str:
    return secrets.token_urlsafe(32)


class OAuthStateStore:
    """Redis-backed, single-use store for the in-flight OAuth transaction (F2/F5).

    The `state` value is also mirrored into a browser cookie by the route layer;
    the callback requires cookie == query == this store before proceeding.
    """

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds

    async def put(self, state: str, value: OAuthState) -> None:
        payload = json.dumps(
            {
                "code_verifier": value.code_verifier,
                "nonce": value.nonce,
                "mode": value.mode,
                "user_id": value.user_id,
            }
        )
        await get_redis().set(_STATE_PREFIX + state, payload, ex=self._ttl)

    async def pop(self, state: str) -> OAuthState | None:
        """Atomically read-and-delete (single-use). Returns None if absent/expired."""
        raw = await get_redis().getdel(_STATE_PREFIX + state)
        if raw is None:
            return None
        data = json.loads(raw)
        return OAuthState(
            code_verifier=data["code_verifier"],
            nonce=data["nonce"],
            mode=data["mode"],
            user_id=data.get("user_id"),
        )


# One process-wide JWKS client so Google's signing keys are cached across
# requests (it refetches only on an unknown `kid`). Constructed lazily.
_jwks_client: PyJWKClient | None = None


def _get_jwks_client(timeout_s: float) -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(_JWKS_URL, timeout=int(timeout_s))
    return _jwks_client


class GoogleOidcClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        timeout_s: float,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._timeout = timeout_s

    def build_authorize_url(self, *, state: str, code_challenge: str, nonce: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": _SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "nonce": nonce,
            # Server-side flow: no long-lived refresh needed, request only sign-in.
            "access_type": "online",
        }
        return f"{_AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> str:
        """Exchange an authorization code for the raw `id_token` (server-side)."""
        data = {
            "code": code,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "redirect_uri": self._redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(_TOKEN_URL, data=data)
        except httpx.HTTPError as exc:
            raise GoogleOAuthUnreachable("token endpoint unreachable") from exc
        if resp.status_code != 200:
            # Do not include the response body — it can echo the code.
            raise GoogleOAuthError(f"token exchange rejected ({resp.status_code})")
        id_token = resp.json().get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise GoogleOAuthError("token response missing id_token")
        return id_token

    async def verify_id_token(self, id_token: str, *, nonce: str) -> GoogleProfile:
        """Verify signature (RS256/JWKS) + aud/iss/exp/nonce, return the profile."""
        try:
            # PyJWKClient uses a blocking urllib fetch; keep it off the event loop.
            signing_key = await asyncio.to_thread(
                _get_jwks_client(self._timeout).get_signing_key_from_jwt, id_token
            )
            claims: dict[str, Any] = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],  # pinned — blocks alg=none / HS256 confusion
                audience=self._client_id,
                options={"require": ["exp", "iat", "aud", "iss", "sub"], "verify_iss": False},
            )
        except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
            raise GoogleOAuthError("id_token verification failed") from exc

        # iss is checked manually so both of Google's historical issuers pass.
        if claims.get("iss") not in _ISSUERS:
            raise GoogleOAuthError("id_token issuer mismatch")
        # Replay defense: the nonce must match the one bound to this state.
        if claims.get("nonce") != nonce:
            raise GoogleOAuthError("id_token nonce mismatch")

        email = claims.get("email")
        sub = claims.get("sub")
        if not isinstance(sub, str) or not isinstance(email, str):
            raise GoogleOAuthError("id_token missing sub/email")
        return GoogleProfile(
            sub=sub,
            email=email,
            email_verified=bool(claims.get("email_verified", False)),
            name=claims.get("name") if isinstance(claims.get("name"), str) else None,
        )


def google_configured() -> bool:
    return bool(get_settings().oauth.google_client_id)


def build_google_client(public_origin: str) -> GoogleOidcClient:
    """Assemble the client from settings (client id + timeout) and Vault (secret).

    Raises :class:`GoogleOAuthNotConfigured` when the client id or Vault secret is
    missing, so the route layer fails closed rather than 500-ing."""
    cfg = get_settings().oauth
    if not cfg.google_client_id:
        raise GoogleOAuthNotConfigured("SMAP_OAUTH googleclientid unset")
    try:
        creds = get_vault_client().kv_get(_VAULT_KV_PATH)
    except Exception as exc:
        # Any Vault failure means "not configured" — fail closed, do not 500.
        raise GoogleOAuthNotConfigured("google_oauth secret unreadable") from exc
    client_secret = str(creds.get("client_secret", "")) or None
    if not client_secret:
        raise GoogleOAuthNotConfigured("google_oauth client_secret missing")
    redirect_uri = public_origin.rstrip("/") + cfg.google_redirect_path
    return GoogleOidcClient(
        client_id=cfg.google_client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        timeout_s=cfg.http_timeout_s,
    )


def state_store() -> OAuthStateStore:
    return OAuthStateStore(get_settings().oauth.state_ttl_seconds)


__all__ = [
    "GoogleOAuthError",
    "GoogleOAuthNotConfigured",
    "GoogleOAuthUnreachable",
    "GoogleOidcClient",
    "GoogleProfile",
    "OAuthState",
    "OAuthStateStore",
    "build_google_client",
    "generate_pkce_pair",
    "google_configured",
    "new_nonce",
    "new_state",
    "state_store",
]
