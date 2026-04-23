"""Access-token signer / verifier — wraps Vault Transit (R6.03).

The asymmetric crypto happens inside Vault (`smap-jwt-sign`). This module is
a thin typed facade over `VaultClient.sign_jwt / verify_jwt` that adds:

- Standard claim set (`iss`, `aud`, `sub`, `iat`, `exp`, `nbf`, `jti`, plus
  the SMAP extension claims `session_id` and `role` for rapid AuthN/AuthZ
  without a DB round-trip).
- Expiry and audience validation (Vault only attests the signature — temporal
  and semantic checks live here so every JWT-consumer path agrees).
- JTI generation (uuid4) so callers never need to invent it.

SoC: no `FastAPI`, no HTTPX here. `shared_kernel.auth.dependencies` decides
how to translate a verification failure into an HTTP response.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from app.config.settings import JwtSection, get_settings
from shared_kernel.auth.clients import get_vault_client, now
from shared_kernel.infra.vault import VaultError

_LEEWAY: Final[timedelta] = timedelta(seconds=5)
_DEFAULT_ROLE: Final = "user"


class JwtError(ValueError):
    """Raised for any access-token failure the handler should translate to 401."""


@dataclass(frozen=True, slots=True)
class AccessClaims:
    sub: uuid.UUID                # user id
    session_id: uuid.UUID
    jti: uuid.UUID
    exp: datetime
    iat: datetime
    role: str                     # quick AuthN role; authoritative check = permissions.py
    is_admin: bool
    impersonated_by: uuid.UUID | None = None

    def remaining_ttl(self, *, now_: datetime | None = None) -> timedelta:
        return self.exp - (now_ or now())


def _cfg() -> JwtSection:
    return get_settings().jwt


def sign_access_token(
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    role: str = _DEFAULT_ROLE,
    is_admin: bool = False,
    extra: dict[str, Any] | None = None,
) -> tuple[str, AccessClaims]:
    """Mint a short-lived access token and return (jwt, claim-record)."""
    cfg = _cfg()
    issued = now()
    exp = issued + timedelta(seconds=cfg.access_ttl_seconds)
    jti = uuid.uuid4()
    claims: dict[str, Any] = {
        "iss": cfg.issuer,
        "aud": cfg.audience,
        "sub": str(user_id),
        "iat": int(issued.timestamp()),
        "nbf": int(issued.timestamp()),
        "exp": int(exp.timestamp()),
        "jti": str(jti),
        "sid": str(session_id),
        "rol": role,
        "adm": bool(is_admin),
    }
    if extra:
        claims.update(extra)
    token = get_vault_client().sign_jwt(claims)
    return token, AccessClaims(
        sub=user_id,
        session_id=session_id,
        jti=jti,
        exp=exp,
        iat=issued,
        role=role,
        is_admin=is_admin,
    )


def verify_access_token(token: str) -> AccessClaims:
    """Signature + audience + expiry check. Raises JwtError on any failure."""
    cfg = _cfg()
    try:
        claims = get_vault_client().verify_jwt(token)
    except VaultError as exc:
        raise JwtError(str(exc)) from exc

    try:
        iss = claims["iss"]
        aud = claims["aud"]
        sub = uuid.UUID(claims["sub"])
        sid = uuid.UUID(claims["sid"])
        jti = uuid.UUID(claims["jti"])
        iat = datetime.fromtimestamp(int(claims["iat"]), tz=now().tzinfo)
        exp = datetime.fromtimestamp(int(claims["exp"]), tz=now().tzinfo)
    except (KeyError, ValueError, TypeError) as exc:
        raise JwtError(f"malformed claim set: {exc}") from exc

    if iss != cfg.issuer:
        raise JwtError(f"issuer mismatch: {iss!r}")
    if aud != cfg.audience:
        raise JwtError(f"audience mismatch: {aud!r}")
    current = now()
    if exp + _LEEWAY < current:
        raise JwtError("access token expired")
    if "nbf" in claims:
        nbf = datetime.fromtimestamp(int(claims["nbf"]), tz=current.tzinfo)
        if current + _LEEWAY < nbf:
            raise JwtError("access token not yet valid")

    imp_raw = claims.get("impersonated_by")
    impersonated_by: uuid.UUID | None = None
    if imp_raw:
        try:
            impersonated_by = uuid.UUID(str(imp_raw))
        except (ValueError, TypeError):
            pass

    return AccessClaims(
        sub=sub,
        session_id=sid,
        jti=jti,
        exp=exp,
        iat=iat,
        role=str(claims.get("rol", _DEFAULT_ROLE)),
        is_admin=bool(claims.get("adm", False)),
        impersonated_by=impersonated_by,
    )


__all__ = ["AccessClaims", "JwtError", "sign_access_token", "verify_access_token"]
