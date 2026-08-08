"""First-party in-process activity validators, registered at app startup.

Lives outside ``contexts/activities`` so the context stays domain-free (its
registry docstring mandates registration from a site under ``app/plugins/``).
:func:`register_first_party_validators` is invoked both at import (registration is
a startup side effect) and explicitly by the bootstrap step, so it is idempotent by
design — re-registering the same id overwrites the identical entry. Tests that
``clear_registry()`` call it again to restore the shipped set.

Two validators ship today, neither of which knows anything about a project's domain:

- ``exact_match`` — a deterministic scorer comparing one payload field to an expected
  answer held in the type's ``validator_config``.
- ``filled_count`` — scores *completeness* rather than correctness, for activities whose
  responses have no answer key ([R30.27]).

Neither needs a DB session, but both keep the standard scorer signature.
"""

from __future__ import annotations

from typing import Any

from contexts.activities.application.validators.registry import register_in_process_validator
from contexts.activities.domain.errors import ValidatorConfigInvalid
from contexts.activities.domain.models import ActivityType, ValidationResult

EXACT_MATCH_ID = "exact_match"
_MISMATCH = "mismatch"

FILLED_COUNT_ID = "filled_count"
_TOO_FEW_FILLED = "too_few_filled"


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


def _is_filled(value: Any) -> bool:
    """Whether one payload value counts as answered.

    A whitespace-only string is blank, not an answer. Numbers and booleans always
    count: the generic schema form submits a boolean for every declared boolean
    property whether or not the participant touched it (``schemaFields.ts``
    ``assemblePayload``), so ``filled_count`` is meant for text-response schemas —
    on a schema carrying booleans the count is inflated by construction.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list | dict):
        return len(value) > 0
    return True


def filled_count_scorer(payload: dict[str, Any], activity_type: ActivityType, *, db: Any) -> ValidationResult:
    """Score completeness: how many payload fields carry an answer ([R30.27]).

    ``min_filled`` of 0 is legal and yields a collect-only activity — every
    schema-valid submission is valid. ``sub_scores`` carries the count and nothing
    else: it is participant-visible, while ``validator_config`` is owner-confidential
    ([R30.25]), so no config value is ever copied into it.
    """
    min_filled = int(activity_type.validator_config.get("min_filled", 0))
    filled = sum(1 for value in payload.values() if _is_filled(value))
    sub_scores: dict[str, Any] = {"filled": filled}

    if filled >= min_filled:
        return ValidationResult(is_valid=True, sub_scores=sub_scores)
    return ValidationResult(is_valid=False, error_class=_TOO_FEW_FILLED, sub_scores=sub_scores)


def validate_filled_count_config(config: dict[str, Any]) -> None:
    """Reject a malformed ``filled_count`` config at registration/edit time.

    ``bool`` is excluded explicitly: it is a subclass of ``int`` in Python, so
    ``True`` would otherwise pass as the threshold 1.
    """
    min_filled = config.get("min_filled")
    if isinstance(min_filled, bool) or not isinstance(min_filled, int):
        raise ValidatorConfigInvalid("filled_count validator requires an integer 'min_filled'")
    if min_filled < 0:
        raise ValidatorConfigInvalid("filled_count validator requires a non-negative 'min_filled'")


def register_first_party_validators() -> None:
    register_in_process_validator(
        EXACT_MATCH_ID,
        exact_match_scorer,
        title="Exact match",
        config_validator=validate_exact_match_config,
    )
    register_in_process_validator(
        FILLED_COUNT_ID,
        filled_count_scorer,
        title="Filled count",
        config_validator=validate_filled_count_config,
    )


register_first_party_validators()


__all__ = [
    "EXACT_MATCH_ID",
    "FILLED_COUNT_ID",
    "exact_match_scorer",
    "filled_count_scorer",
    "register_first_party_validators",
    "validate_exact_match_config",
    "validate_filled_count_config",
]
