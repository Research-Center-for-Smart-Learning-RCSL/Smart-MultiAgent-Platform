"""`TokenPairOut` must be buildable from a slotted `TokenPair` (API-11).

Regression guard: `TokenPair` is a `@dataclass(slots=True)`, so its instances
have no `__dict__`. The router previously did `TokenPairOut(**pair.__dict__)`,
which raised `AttributeError` -> unhandled 500 on every successful login and
refresh. `_token_pair_out` maps the fields explicitly instead.
"""

from __future__ import annotations

import pytest

from app.api.v1.auth import TokenPairOut, _token_pair_out
from contexts.identity.application.auth_service import TokenPair


def test_token_pair_instance_has_no_dict() -> None:
    # The precondition the bug hinged on. If `TokenPair` ever drops
    # `slots=True`, this assertion flips and the regression is moot.
    pair = TokenPair(access_token="a", refresh_token="r", token_type="Bearer", expires_in=900)
    with pytest.raises(AttributeError):
        _ = pair.__dict__


def test_token_pair_out_maps_every_field() -> None:
    pair = TokenPair(
        access_token="access-xyz",
        refresh_token="refresh-abc",
        token_type="Bearer",
        expires_in=900,
    )
    out = _token_pair_out(pair)
    assert isinstance(out, TokenPairOut)
    assert out.access_token == "access-xyz"
    assert out.refresh_token == "refresh-abc"
    assert out.token_type == "Bearer"
    assert out.expires_in == 900
