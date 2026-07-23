"""First-party in-process activity validators, registered at app startup.

Lives outside ``contexts/activities`` so the context stays domain-free (its
registry docstring mandates registration from a site under ``app/plugins/``).
:func:`register_first_party_validators` is invoked both at import (registration is
a startup side effect) and explicitly by the bootstrap step, so it is idempotent by
design — re-registering the same id overwrites the identical entry. Tests that
``clear_registry()`` call it again to restore the shipped set.

v1 ships ``exact_match``: a deterministic scorer comparing one payload field to an
expected answer held in the type's ``validator_config``. It needs no DB session but
keeps the standard scorer signature.
"""

from __future__ import annotations

from typing import Any

from contexts.activities.application.validators.registry import register_in_process_validator
from contexts.activities.domain.errors import ValidatorConfigInvalid
from contexts.activities.domain.models import ActivityType, ValidationResult

EXACT_MATCH_ID = "exact_match"
_MISMATCH = "mismatch"


def exact_match_scorer(payload: dict[str, Any], activity_type: ActivityType, *, db: Any) -> ValidationResult:
    """Compare ``payload[field]`` to the type's ``expected`` value.

    The payload is already schema-valid at dispatch (the submission service
    validates it first), so a missing ``field`` yields ``None`` and a plain
    mismatch rather than an error. String comparison honours ``case_sensitive``
    (default false); non-string values compare by equality.
    """
    config = activity_type.validator_config
    field = str(config.get("field", ""))
    expected = config.get("expected")
    actual = payload.get(field)
    case_sensitive = bool(config.get("case_sensitive", False))

    if isinstance(actual, str) and isinstance(expected, str) and not case_sensitive:
        is_valid = actual.casefold() == expected.casefold()
    else:
        is_valid = actual == expected

    if is_valid:
        return ValidationResult(is_valid=True)
    return ValidationResult(is_valid=False, error_class=_MISMATCH)


def validate_exact_match_config(config: dict[str, Any]) -> None:
    """Reject a malformed ``exact_match`` config at registration/edit time."""
    field = config.get("field")
    if not isinstance(field, str) or not field.strip():
        raise ValidatorConfigInvalid("exact_match validator requires a non-empty 'field'")
    if config.get("expected") is None:
        raise ValidatorConfigInvalid("exact_match validator requires an 'expected' value")


def register_first_party_validators() -> None:
    register_in_process_validator(
        EXACT_MATCH_ID,
        exact_match_scorer,
        title="Exact match",
        config_validator=validate_exact_match_config,
    )


register_first_party_validators()


__all__ = [
    "EXACT_MATCH_ID",
    "exact_match_scorer",
    "register_first_party_validators",
    "validate_exact_match_config",
]
