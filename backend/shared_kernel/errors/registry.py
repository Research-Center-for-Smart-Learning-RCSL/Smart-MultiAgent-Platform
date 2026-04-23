"""Seed registry of RFC 7807 problem slugs used in Phase A.

Later phases register additional slugs through `docs/operations.md` §6
(the operator-facing catalog). This module's role is to centralise slug
constants so typos trip mypy rather than runtime.
"""

from __future__ import annotations

from typing import Final

from shared_kernel.errors.problem import problem_type

NOT_IMPLEMENTED: Final = problem_type("not-implemented")
INTERNAL: Final = problem_type("internal")
VALIDATION: Final = problem_type("validation")
RATE_LIMITED: Final = problem_type("rate-limited")
AUTH_TOKEN_EXPIRED: Final = problem_type("auth/token-expired")
DEPENDENCY_UNAVAILABLE: Final = problem_type("dependency-unavailable")

__all__ = [
    "AUTH_TOKEN_EXPIRED",
    "DEPENDENCY_UNAVAILABLE",
    "INTERNAL",
    "NOT_IMPLEMENTED",
    "RATE_LIMITED",
    "VALIDATION",
]
