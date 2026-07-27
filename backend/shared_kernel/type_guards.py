"""Tiny, framework-free type predicates shared across layers.

Deliberately has zero imports beyond stdlib (unlike `shared_kernel.validation`,
which pulls in Pydantic) so `contexts/*/domain/` modules can import it without
violating the "domain is framework-free" rule in `backend/CLAUDE.md`.
"""

from __future__ import annotations

from typing import TypeGuard


def is_plain_int(value: object) -> TypeGuard[int]:
    """True if `value` is a genuine `int`, excluding `bool`.

    `bool` is an `int` subclass in Python (`isinstance(True, int)` is `True`,
    `int(True) == 1`), so a bare `isinstance(value, int)` check silently accepts
    `True`/`False` wherever an integer is expected. That is never the intent for
    a JSON-sourced numeric field: a wrong-typed `bool` must be rejected or
    resolved to a default like any other wrong-typed value, not read as 0/1.

    Typed as a `TypeGuard` (not a bare `bool`) so callers under `mypy --strict`
    get `value` narrowed to `int` in the guarded branch, instead of an `Any`
    leaking out of the `if`.
    """
    return isinstance(value, int) and not isinstance(value, bool)


__all__ = ["is_plain_int"]
