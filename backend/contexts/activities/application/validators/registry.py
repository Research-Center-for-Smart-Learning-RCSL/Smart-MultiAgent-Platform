"""In-process validator registry (activities-platform-core §5.3).

The platform ships **zero** domain validators from within this context. First-party
project validators register at app startup from a module OUTSIDE
``contexts/activities`` (e.g. a project package under ``app/plugins/``), keeping the
context domain-free.

A scorer has signature ``fn(payload, activity_type, *, db) -> ValidationResult`` and
may be sync or async; :func:`run_in_process_scorer` awaits it if needed. It receives
the live DB session so a first-party function can query project-owned data tables
while the port stays generic.

A validator may also register a ``config_validator`` — a ``fn(validator_config)``
that raises ``ValidatorConfigInvalid`` when a type's config is malformed for it
(e.g. ``exact_match`` needs ``field``/``expected``). ``type_service`` calls it at
registration/edit so a broken config is rejected at the API boundary rather than
deferred to a per-submission ``error`` verdict.

Trust note: an in-process validator is first-party backend code running in the app
process with a live DB session — the same trust tier as any backend module. Register
only validators you ship; untrusted validators use the MCP sandbox.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from contexts.activities.domain.models import ActivityType, ValidationResult

InProcessScorer = Callable[..., ValidationResult | Awaitable[ValidationResult]]
# Raises ``ValidatorConfigInvalid`` on a malformed ``validator_config``; returns None.
ConfigValidator = Callable[[dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class ValidatorInfo:
    """Public listing metadata for a registered validator (no scoring code)."""

    validator_id: str
    title: str


@dataclass(frozen=True, slots=True)
class _Registered:
    fn: InProcessScorer
    title: str
    config_validator: ConfigValidator | None


_REGISTRY: dict[str, _Registered] = {}


def register_in_process_validator(
    validator_id: str,
    fn: InProcessScorer,
    *,
    title: str | None = None,
    config_validator: ConfigValidator | None = None,
) -> None:
    _REGISTRY[validator_id] = _Registered(
        fn=fn, title=title or validator_id, config_validator=config_validator
    )


def is_registered(validator_id: str) -> bool:
    return validator_id in _REGISTRY


def list_registered() -> list[ValidatorInfo]:
    """The registered validators as ``(validator_id, title)``, sorted by id for a
    stable listing. The single source the authoring form's picker draws from."""
    return [ValidatorInfo(validator_id=vid, title=entry.title) for vid, entry in sorted(_REGISTRY.items())]


def get_config_validator(validator_id: str) -> ConfigValidator | None:
    entry = _REGISTRY.get(validator_id)
    return entry.config_validator if entry is not None else None


def clear_registry() -> None:
    """Test hook — reset the process-global registry."""
    _REGISTRY.clear()


async def run_in_process_scorer(
    validator_id: str, payload: dict[str, Any], activity_type: ActivityType, *, db: Any
) -> ValidationResult:
    entry = _REGISTRY.get(validator_id)
    if entry is None:
        raise KeyError(validator_id)
    result = entry.fn(payload, activity_type, db=db)
    if inspect.isawaitable(result):
        return await result
    return result


__all__ = [
    "ConfigValidator",
    "InProcessScorer",
    "ValidatorInfo",
    "clear_registry",
    "get_config_validator",
    "is_registered",
    "list_registered",
    "register_in_process_validator",
    "run_in_process_scorer",
]
