"""Guest JWT sign/verify (AC-2).

Tests that sign_guest_token produces a JWT with the correct claims and that
verify_guest_token validates them, using the same _FakeVault pattern as
test_jwt_nbf_required.py.
"""

from __future__ import annotations

import time
import uuid

import pytest

from app.config.settings import get_settings
from shared_kernel.auth import jwt as jwtmod


class _FakeVault:
    def __init__(self, claims: dict | None = None) -> None:
        self._claims = claims
        self.last_signed: dict | None = None

    def sign_jwt(self, claims: dict) -> str:
        self.last_signed = dict(claims)
        return "fake-guest-token"

    def verify_jwt(self, token: str) -> dict:
        assert self._claims is not None
        return self._claims


def _guest_claims(*, overrides: dict | None = None) -> dict:
    cfg = get_settings().jwt
    nowts = int(time.time())
    base = {
        "iss": cfg.issuer,
        "aud": cfg.audience,
        "sub": str(uuid.uuid4()),
        "jti": str(uuid.uuid4()),
        "iat": nowts,
        "nbf": nowts,
        "exp": nowts + cfg.guest_access_ttl_seconds,
        "token_use": "guest_access",
        "rol": "guest",
        "chatroom_id": str(uuid.uuid4()),
        "display_name": "Test Guest",
    }
    if overrides:
        base.update(overrides)
    return base


# -- sign_guest_token --


def test_sign_produces_correct_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _FakeVault()
    monkeypatch.setattr(jwtmod, "get_vault_client", lambda: vault)

    gs_id = uuid.uuid4()
    cr_id = uuid.uuid4()
    token, claims = jwtmod.sign_guest_token(
        guest_session_id=gs_id,
        chatroom_id=cr_id,
        display_name="Alice",
    )

    assert token == "fake-guest-token"
    assert claims.guest_session_id == gs_id
    assert claims.chatroom_id == cr_id
    assert claims.display_name == "Alice"

    signed = vault.last_signed
    assert signed is not None
    assert signed["sub"] == str(gs_id)
    assert signed["chatroom_id"] == str(cr_id)
    assert signed["display_name"] == "Alice"
    assert signed["token_use"] == "guest_access"
    assert signed["rol"] == "guest"


def test_sign_uses_guest_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _FakeVault()
    monkeypatch.setattr(jwtmod, "get_vault_client", lambda: vault)

    jwtmod.sign_guest_token(
        guest_session_id=uuid.uuid4(),
        chatroom_id=uuid.uuid4(),
        display_name="Bob",
    )

    signed = vault.last_signed
    assert signed is not None
    ttl = signed["exp"] - signed["iat"]
    assert ttl == get_settings().jwt.guest_access_ttl_seconds


# -- verify_guest_token --


def test_verify_valid_guest_token(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = _guest_claims()
    monkeypatch.setattr(jwtmod, "get_vault_client", lambda: _FakeVault(claims))

    result = jwtmod.verify_guest_token("opaque")
    assert result.guest_session_id == uuid.UUID(claims["sub"])
    assert result.chatroom_id == uuid.UUID(claims["chatroom_id"])
    assert result.display_name == claims["display_name"]


def test_verify_rejects_access_token_use(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = _guest_claims(overrides={"token_use": "access"})
    monkeypatch.setattr(jwtmod, "get_vault_client", lambda: _FakeVault(claims))

    with pytest.raises(jwtmod.JwtError, match="guest_access"):
        jwtmod.verify_guest_token("opaque")


def test_verify_rejects_expired_guest_token(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = _guest_claims(
        overrides={
            "iat": int(time.time()) - 7200,
            "nbf": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
        }
    )
    monkeypatch.setattr(jwtmod, "get_vault_client", lambda: _FakeVault(claims))

    with pytest.raises(jwtmod.JwtError, match="expired"):
        jwtmod.verify_guest_token("opaque")


def test_verify_rejects_wrong_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = _guest_claims(overrides={"iss": "evil.local"})
    monkeypatch.setattr(jwtmod, "get_vault_client", lambda: _FakeVault(claims))

    with pytest.raises(jwtmod.JwtError, match="issuer"):
        jwtmod.verify_guest_token("opaque")


def test_verify_rejects_wrong_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = _guest_claims(overrides={"aud": "wrong.api"})
    monkeypatch.setattr(jwtmod, "get_vault_client", lambda: _FakeVault(claims))

    with pytest.raises(jwtmod.JwtError, match="audience"):
        jwtmod.verify_guest_token("opaque")


def test_verify_rejects_missing_chatroom_id(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = _guest_claims()
    del claims["chatroom_id"]
    monkeypatch.setattr(jwtmod, "get_vault_client", lambda: _FakeVault(claims))

    with pytest.raises(jwtmod.JwtError, match="malformed"):
        jwtmod.verify_guest_token("opaque")


def test_verify_rejects_missing_nbf(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = _guest_claims()
    del claims["nbf"]
    monkeypatch.setattr(jwtmod, "get_vault_client", lambda: _FakeVault(claims))

    with pytest.raises(jwtmod.JwtError):
        jwtmod.verify_guest_token("opaque")


def test_verify_rejects_future_iat(monkeypatch: pytest.MonkeyPatch) -> None:
    future = int(time.time()) + 3600
    claims = _guest_claims(
        overrides={
            "iat": future,
            "nbf": future,
            "exp": future + 14400,
        }
    )
    monkeypatch.setattr(jwtmod, "get_vault_client", lambda: _FakeVault(claims))

    with pytest.raises(jwtmod.JwtError, match="future"):
        jwtmod.verify_guest_token("opaque")
