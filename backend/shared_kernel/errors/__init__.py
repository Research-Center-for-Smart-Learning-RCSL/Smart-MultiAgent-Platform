"""RFC 7807 application/problem+json primitives.

Canonical URL prefix (R19.06 / docs/operations.md §6):
    https://smap.local/problems/...
"""

from shared_kernel.errors.problem import (
    NotImplementedProblem,
    Problem,
    SmapError,
    problem_type,
)

__all__ = [
    "NotImplementedProblem",
    "Problem",
    "SmapError",
    "problem_type",
]
