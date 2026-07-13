"""In-process validator registry (activities-platform-core §5.3).

The platform ships **zero** domain validators. First-party project validators
register at app startup from a module OUTSIDE ``contexts/activities`` (e.g. a
project package under ``app/plugins/``), keeping the context domain-free.

A scorer has signature ``fn(payload, activity_type, *, db) -> ValidationResult``
and may be sync or async; :func:`run_in_process_scorer` awaits it if needed. It
receives the live DB session so a first-party function can query project-owned
data tables while the port stays generic.

Trust note: an in-process validator is first-party backend code running in the
app process with a live DB session — the same trust tier as any backend module.
Register only validators you ship; untrusted validators use the MCP sandbox.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from contexts.activities.domain.models import ActivityType, ValidationResult

InProcessScorer = Callable[..., ValidationResult | Awaitable[ValidationResult]]

_REGISTRY: dict[str, InProcessScorer] = {}


def register_in_process_validator(validator_id: str, fn: InProcessScorer) -> None:
    _REGISTRY[validator_id] = fn


def is_registered(validator_id: str) -> bool:
    return validator_id in _REGISTRY


def clear_registry() -> None:
    """Test hook — reset the process-global registry."""
    _REGISTRY.clear()


async def run_in_process_scorer(
    validator_id: str, payload: dict[str, Any], activity_type: ActivityType, *, db: Any
) -> ValidationResult:
    fn = _REGISTRY.get(validator_id)
    if fn is None:
        raise KeyError(validator_id)
    result = fn(payload, activity_type, db=db)
    if inspect.isawaitable(result):
        return await result
    return result


__all__ = [
    "InProcessScorer",
    "clear_registry",
    "is_registered",
    "register_in_process_validator",
    "run_in_process_scorer",
]
