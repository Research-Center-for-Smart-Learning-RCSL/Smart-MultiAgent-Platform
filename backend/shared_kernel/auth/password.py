"""Argon2id password hashing + policy (R6.01).

Parameters are fixed by spec: memory 64 MiB, time cost 3, parallelism **2**
(not 4 — R6.01 is explicit). `PasswordHasher` reuses a single argon2-cffi
instance per process; `verify_and_upgrade` auto-rehashes when it detects the
stored hash was produced with weaker parameters than the current policy.

SoC: domain-independent; imported from both the identity context and the
permission-test harnesses. Does NOT import anything from contexts.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError

_MEMORY_KIB: Final = 64 * 1024  # 64 MiB
_TIME_COST: Final = 3
_PARALLELISM: Final = 2
_HASH_LEN: Final = 32
_SALT_LEN: Final = 16

_MIN_LEN: Final = 10
_MAX_LEN: Final = 1024  # bound argon2 cost on pathological inputs
_LETTER_RE: Final = re.compile(r"[A-Za-z]")
_DIGIT_RE: Final = re.compile(r"[0-9]")
_SYMBOL_RE: Final = re.compile(r"[^A-Za-z0-9]")


class PasswordPolicyError(ValueError):
    """Raised when a candidate password does not satisfy R6.01."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class PasswordVerification:
    ok: bool
    rehashed: str | None  # new hash if params were upgraded, else None


class PasswordHasher:
    """Argon2id wrapper — stateless; safe to share across threads."""

    def __init__(self) -> None:
        self._ph = _Argon2(
            time_cost=_TIME_COST,
            memory_cost=_MEMORY_KIB,
            parallelism=_PARALLELISM,
            hash_len=_HASH_LEN,
            salt_len=_SALT_LEN,
        )

    def hash(self, plaintext: str) -> str:
        validate_password(plaintext)
        # Normalise to NFKC so "café" and "cafe\u0301" hash identically.
        return self._ph.hash(unicodedata.normalize("NFKC", plaintext))

    def verify(self, hashed: str, plaintext: str) -> PasswordVerification:
        normalised = unicodedata.normalize("NFKC", plaintext)
        try:
            self._ph.verify(hashed, normalised)
        except (VerifyMismatchError, VerificationError, InvalidHash):
            return PasswordVerification(ok=False, rehashed=None)

        if self._ph.check_needs_rehash(hashed):
            return PasswordVerification(ok=True, rehashed=self._ph.hash(normalised))
        return PasswordVerification(ok=True, rehashed=None)


def validate_password(candidate: str) -> None:
    """R6.01 policy — length, letter, digit, symbol."""
    if not isinstance(candidate, str):
        raise PasswordPolicyError("type", "password must be a string")
    # NFKC before length check so homograph tricks don't bypass bounds.
    normalised = unicodedata.normalize("NFKC", candidate)
    if len(normalised) < _MIN_LEN:
        raise PasswordPolicyError("too_short", f"password must be ≥ {_MIN_LEN} characters")
    if len(normalised) > _MAX_LEN:
        raise PasswordPolicyError("too_long", f"password must be ≤ {_MAX_LEN} characters")
    if not _LETTER_RE.search(normalised):
        raise PasswordPolicyError("no_letter", "password must contain at least one letter")
    if not _DIGIT_RE.search(normalised):
        raise PasswordPolicyError("no_digit", "password must contain at least one digit")
    if not _SYMBOL_RE.search(normalised):
        raise PasswordPolicyError("no_symbol", "password must contain at least one symbol")


# Pre-computed Argon2id hash of a throwaway (policy-valid) password. The login
# path verifies a *non-existent* account's submitted password against this so
# the "no such user" branch pays the same ~64 MiB / t=3 cost as a real verify —
# closing the timing oracle that otherwise reveals which emails have accounts
# (SEC-M3). Computed once at import; the ~tens-of-ms cost is paid at startup.
_DUMMY_PASSWORD: Final = "Tldr-NoSuchUser-9!"  # satisfies R6.01 so hash() accepts it
DUMMY_HASH: Final = PasswordHasher().hash(_DUMMY_PASSWORD)


__all__ = [
    "DUMMY_HASH",
    "PasswordHasher",
    "PasswordPolicyError",
    "PasswordVerification",
    "validate_password",
]
