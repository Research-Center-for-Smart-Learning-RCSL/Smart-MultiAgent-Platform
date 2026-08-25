"""First-party in-process activity validators, registered at app startup.

Lives outside ``contexts/activities`` so the context stays domain-free (its
registry docstring mandates registration from a site under ``app/plugins/``).
:func:`register_first_party_validators` is invoked both at import (registration is
a startup side effect) and explicitly by the bootstrap step, so it is idempotent by
design — re-registering the same id overwrites the identical entry. Tests that
``clear_registry()`` call it again to restore the shipped set.

Three validators ship today, none of which knows anything about a project's domain:

- ``exact_match`` — a deterministic scorer comparing one payload field to an expected
  answer held in the type's ``validator_config``.
- ``filled_count`` — scores *completeness* rather than correctness, for activities whose
  responses have no answer key ([R30.27]).
- ``filled_count_coverage`` — the same verdict, plus *which* declared fields carry an
  answer ([R28.17]). A type opts into it to make per-field coverage a server-computed
  fact; ``filled_count`` is deliberately left alone.

None needs a DB session, but all keep the standard scorer signature.
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

FILLED_COUNT_COVERAGE_ID = "filled_count_coverage"
#: ``sub_scores`` key carrying the declared property *names* that were answered.
#: Read by the room-scoped coverage aggregates ([R28.17]); a type whose submissions
#: do not carry it has no coverage to report and the tool refuses the block.
FILLED_FIELDS_KEY = "filled_fields"


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
    """Whether one payload value counts as answered ([R30.27]: non-empty fields).

    ``False`` does **not** count. The generic schema form submits a boolean for
    every declared boolean property whether or not the participant touched it
    (``schemaFields.ts`` ``assemblePayload`` writes ``v === true`` unconditionally),
    so an unticked box is indistinguishable from an untouched one. Counting it
    would let a submission with nothing filled in at all score
    ``filled == len(properties)`` and pass any threshold — the metric would report
    the schema's size rather than the participant's effort.

    The cost is that a deliberate "no" is not counted as an answer. That is the
    right trade for a *completeness* measure, which is what this validator is.

    Numbers still count, including ``0``: a numeric field is only present when the
    participant typed something.
    """
    if value is None or value is False:
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

    Only properties the type *declares* are counted. JSON Schema allows additional
    properties unless a schema forbids them, so counting ``payload.values()`` would
    let a participant clear the threshold — and inflate the reported fluency count —
    by padding the submission with keys the activity never asked for. Counting
    declared properties also bounds the work by the owner-authored schema rather
    than by submission width.
    """
    min_filled = int(activity_type.validator_config.get("min_filled", 0))
    declared = activity_type.payload_schema.get("properties") or {}
    filled = sum(1 for name in declared if _is_filled(payload.get(name)))
    sub_scores: dict[str, Any] = {"filled": filled}

    if filled >= min_filled:
        return ValidationResult(is_valid=True, sub_scores=sub_scores)
    return ValidationResult(is_valid=False, error_class=_TOO_FEW_FILLED, sub_scores=sub_scores)


def filled_count_coverage_scorer(
    payload: dict[str, Any], activity_type: ActivityType, *, db: Any
) -> ValidationResult:
    """``filled_count``'s verdict, plus which declared fields were answered ([R28.17]).

    The verdict, the ``error_class`` and the ``filled`` sub-score are identical to
    :func:`filled_count_scorer` for the same payload and config — a type may move
    between the two without any submission changing outcome. What is added is
    ``sub_scores['filled_fields']`` and a ``detail``.

    **Field names, never field values.** The names come from the owner-authored
    ``payload_schema``; the participant's own words are read only through
    :func:`_is_filled`, which returns a boolean. Setting ``detail`` also means
    ``build_agent_digest`` stops falling back to a JSON dump of the payload for this
    type, so an agent reading the recent-activity window sees *less* participant text
    after a type adopts this validator than before.

    Declared order is preserved, so a caller rendering the list beside the schema's
    own field order does not have to re-sort it.
    """
    min_filled = int(activity_type.validator_config.get("min_filled", 0))
    declared = activity_type.payload_schema.get("properties") or {}
    filled_fields = [name for name in declared if _is_filled(payload.get(name))]
    sub_scores: dict[str, Any] = {"filled": len(filled_fields), FILLED_FIELDS_KEY: filled_fields}
    detail = _coverage_detail(filled_fields, len(declared))

    if len(filled_fields) >= min_filled:
        return ValidationResult(is_valid=True, sub_scores=sub_scores, detail=detail)
    return ValidationResult(
        is_valid=False, error_class=_TOO_FEW_FILLED, sub_scores=sub_scores, detail=detail
    )


def _coverage_detail(filled_fields: list[str], declared_count: int) -> str:
    """``"3/9 fields answered: home, work, leisure"``, or the no-answer form.

    Ends without a trailing colon when nothing is filled: a dangling ``answered:``
    reads as a truncated line, and this string is shown to an agent as a submission
    digest where "the rest was cut off" is the wrong inference to invite.
    """
    counts = f"{len(filled_fields)}/{declared_count} fields answered"
    if not filled_fields:
        return counts
    return f"{counts}: {', '.join(filled_fields)}"


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


def validate_filled_count_against_schema(config: dict[str, Any], payload_schema: dict[str, Any]) -> None:
    """Reject a ``min_filled`` no submission could ever reach.

    The scorer counts *declared* properties only, so a threshold above that count
    yields an activity nobody can pass. This cannot live in
    :func:`validate_filled_count_config` — that one is handed the config alone,
    which is exactly why the registry carries a second hook.

    A schema with no ``properties`` is left to the well-formedness check that
    already ran; complaining about the threshold there would name the wrong field.
    """
    declared = payload_schema.get("properties")
    if not isinstance(declared, dict):
        return
    min_filled = config.get("min_filled")
    if isinstance(min_filled, bool) or not isinstance(min_filled, int):
        return  # validate_filled_count_config owns the type error
    if min_filled > len(declared):
        raise ValidatorConfigInvalid(
            f"filled_count min_filled is {min_filled}, above the {len(declared)} declared "
            f"propert{'y' if len(declared) == 1 else 'ies'}"
        )


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
        schema_config_validator=validate_filled_count_against_schema,
    )
    # Same config contract as `filled_count`, so both hooks are registered
    # unchanged rather than copied: the two must never disagree about which
    # `min_filled` is legal, or moving a type between them would change whether
    # it can be saved.
    register_in_process_validator(
        FILLED_COUNT_COVERAGE_ID,
        filled_count_coverage_scorer,
        title="Filled count with field coverage",
        config_validator=validate_filled_count_config,
        schema_config_validator=validate_filled_count_against_schema,
    )


register_first_party_validators()


__all__ = [
    "EXACT_MATCH_ID",
    "FILLED_COUNT_COVERAGE_ID",
    "FILLED_COUNT_ID",
    "FILLED_FIELDS_KEY",
    "exact_match_scorer",
    "filled_count_coverage_scorer",
    "filled_count_scorer",
    "register_first_party_validators",
    "validate_exact_match_config",
    "validate_filled_count_against_schema",
    "validate_filled_count_config",
]
