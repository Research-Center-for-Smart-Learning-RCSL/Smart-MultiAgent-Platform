"""`nbf` is a required access-token claim, not optional (SEC-L1).

The verifier used to gate the not-yet-valid check behind `if "nbf" in claims`,
so a token lacking `nbf` skipped it entirely. SMAP always mints `nbf`, so any
token without it is malformed/forged and must be rejected.
"""

from __future__ import annotations

import time
import uuid

import pytest

from app.config.settings import get_settings
from shared_kernel.auth import jwt as jwtmod


class _FakeVault:
    """Stands in for the Vault transit client — `verify_jwt` just returns the
    pre-baked claim set (signature attestation is not what we're testing)."""

    def __init__(self, claims: dict) -> None:
        self._claims = claims

    def verify_jwt(self, token: str) -> dict:
        return self._claims


def _base_claims() -> dict:
    cfg = get_settings().jwt
    nowts = int(time.time())
    return {
        "iss": cfg.issuer,
        "aud": cfg.audience,
        "sub": str(uuid.uuid4()),
        "sid": str(uuid.uuid4()),
        "jti": str(uuid.uuid4()),
        "iat": nowts,
        "nbf": nowts,
        "exp": nowts + 600,
        "token_use": "access",
        "rol": "user",
        "adm": False,
    }


def test_token_without_nbf_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = _base_claims()
    del claims["nbf"]
    monkeypatch.setattr(jwtmod, "get_vault_client", lambda: _FakeVault(claims))
    with pytest.raises(jwtmod.JwtError):
        jwtmod.verify_access_token("opaque-token")


def test_valid_token_with_nbf_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = _base_claims()
    monkeypatch.setattr(jwtmod, "get_vault_client", lambda: _FakeVault(claims))
    out = jwtmod.verify_access_token("opaque-token")
    assert out.role == "user"


def test_future_nbf_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = _base_claims()
    claims["nbf"] = int(time.time()) + 3600  # not valid for another hour
    monkeypatch.setattr(jwtmod, "get_vault_client", lambda: _FakeVault(claims))
    with pytest.raises(jwtmod.JwtError):
        jwtmod.verify_access_token("opaque-token")
