"""R6.01 password policy — accept, reject, NFKC, rehash detection."""

from __future__ import annotations

import pytest

from shared_kernel.auth.password import (
    PasswordHasher,
    PasswordPolicyError,
    validate_password,
)


# Accepts.
def test_policy_accepts_minimal_valid() -> None:
    validate_password("Abcdef1!gh")   # exactly 10 chars, letter+digit+symbol


def test_policy_accepts_unicode_after_nfkc() -> None:
    # Composed form: é = U+00E9; length 10 after NFKC (letter+digit+symbol all present).
    validate_password("Café12345!")


# Rejects.
@pytest.mark.parametrize(
    "bad, code",
    [
        ("Short1!", "too_short"),
        ("nodigitsnow!", "no_digit"),
        ("NoSymbol123", "no_symbol"),
        ("11112222!!", "no_letter"),
    ],
)
def test_policy_rejects(bad: str, code: str) -> None:
    with pytest.raises(PasswordPolicyError) as exc:
        validate_password(bad)
    assert exc.value.code == code


# Round-trip through argon2id.
def test_hasher_round_trip() -> None:
    ph = PasswordHasher()
    hashed = ph.hash("Abcdef1!gh")
    # Confirm spec parameters are embedded: memory=64 MiB, time=3, parallelism=2.
    assert "$argon2id$" in hashed
    assert "m=65536" in hashed
    assert "t=3" in hashed
    assert "p=2" in hashed
    res = ph.verify(hashed, "Abcdef1!gh")
    assert res.ok is True
    assert res.rehashed is None

    res_bad = ph.verify(hashed, "WrongPass1!")
    assert res_bad.ok is False
