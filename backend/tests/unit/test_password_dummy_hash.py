"""The login timing-equaliser hash (SEC-M3).

The "no such account" login branch verifies the submitted password against
`DUMMY_HASH` so it pays the same Argon2id cost as a real verify. These tests
pin that the constant is a real, verifiable Argon2id hash produced with the
current policy parameters (so verifying against it costs the same as a live
account's hash, not a cheap early-out).
"""

from __future__ import annotations

from shared_kernel.auth.password import DUMMY_HASH, PasswordHasher


def test_dummy_hash_is_argon2id() -> None:
    assert DUMMY_HASH.startswith("$argon2id$")
    # Same cost parameters as the live hasher → equal verify latency.
    assert "m=65536" in DUMMY_HASH  # 64 MiB
    assert "t=3" in DUMMY_HASH
    assert "p=2" in DUMMY_HASH


def test_dummy_hash_verifies_and_rejects() -> None:
    hasher = PasswordHasher()
    # A non-matching password (the real "no such user" case) returns not-ok,
    # but only after paying the full verify cost.
    assert hasher.verify(DUMMY_HASH, "definitely-not-the-dummy-9!").ok is False
    # And no spurious rehash is requested (params already current).
    assert hasher.verify(DUMMY_HASH, "definitely-not-the-dummy-9!").rehashed is None
