"""Guest WS auth branch (AC-7).

Verifies that authenticate_subprotocol and refresh_principal construct
guest principals when given guest_access tokens.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared_kernel.auth.jwt import GuestClaims, JwtError
from shared_kernel.auth.permissions import Principal
from shared_kernel.realtime.ws_auth import (
    WsAuthError,
    _authenticate_guest,
    _is_guest_token,
    _refresh_guest,
)


def _make_jwt(token_use: str = "guest_access") -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"token_use": token_use}).encode()
    ).rstrip(b"=")
    return f"{header.decode()}.{payload.decode()}.sig"


# -- _is_guest_token --


def test_is_guest_token_true() -> None:
    assert _is_guest_token(_make_jwt("guest_access")) is True


def test_is_guest_token_false_for_access() -> None:
    assert _is_guest_token(_make_jwt("access")) is False


def test_is_guest_token_false_for_garbage() -> None:
    assert _is_guest_token("not-a-jwt") is False


# -- _authenticate_guest --


def test_authenticate_guest_constructs_principal() -> None:
    gs_id = uuid.uuid4()
    cr_id = uuid.uuid4()
    claims = GuestClaims(
        guest_session_id=gs_id,
        chatroom_id=cr_id,
        display_name="Guest",
        jti=uuid.uuid4(),
        exp=datetime.now(UTC) + timedelta(hours=4),
        iat=datetime.now(UTC),
    )

    with patch("shared_kernel.realtime.ws_auth.jwt") as mock_jwt:
        mock_jwt.verify_guest_token.return_value = claims
        mock_jwt.JwtError = JwtError

        result = _authenticate_guest("fake.token.sig", "ticket.abc")

    assert result.principal.is_guest is True
    assert result.principal.chatroom_id == cr_id
    assert result.principal.user_id == gs_id
    assert result.subprotocol == "ticket.abc"


def test_authenticate_guest_raises_on_bad_token() -> None:
    with patch("shared_kernel.realtime.ws_auth.jwt") as mock_jwt:
        mock_jwt.verify_guest_token.side_effect = JwtError("expired")
        mock_jwt.JwtError = JwtError

        with pytest.raises(WsAuthError, match="invalid guest token"):
            _authenticate_guest("bad.token.sig", "ticket.abc")


# -- _refresh_guest --


def test_refresh_guest_constructs_principal() -> None:
    gs_id = uuid.uuid4()
    cr_id = uuid.uuid4()
    claims = GuestClaims(
        guest_session_id=gs_id,
        chatroom_id=cr_id,
        display_name="Guest",
        jti=uuid.uuid4(),
        exp=datetime.now(UTC) + timedelta(hours=4),
        iat=datetime.now(UTC),
    )

    with patch("shared_kernel.realtime.ws_auth.jwt") as mock_jwt:
        mock_jwt.verify_guest_token.return_value = claims
        mock_jwt.JwtError = JwtError

        result = _refresh_guest("fake.token.sig")

    assert result.principal.is_guest is True
    assert result.principal.chatroom_id == cr_id
