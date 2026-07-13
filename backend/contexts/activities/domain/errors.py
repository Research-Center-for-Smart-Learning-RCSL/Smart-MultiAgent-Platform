"""Structured-activities domain errors → RFC 7807 slugs (Chapter §30).

Errors carry a ``code`` only; the HTTP status for each is assigned in
``interfaces/error_mapping.py`` (the shared context-handler convention).
"""

from __future__ import annotations


class ActivitiesError(Exception):
    code: str = "activities/generic"


class ActivityTypeNotFound(ActivitiesError):
    code = "activities/type-not-found"


class SessionNotFound(ActivitiesError):
    code = "activities/session-not-found"


class ActivityActivationNotFound(ActivitiesError):
    code = "activities/activation-not-found"


class ActivityAlreadyActive(ActivitiesError):
    code = "activities/already-active"


class ActivityNotActive(ActivitiesError):
    code = "activities/not-active"


class SubmissionNotFound(ActivitiesError):
    code = "activities/submission-not-found"


class ActivityTypeKeyConflict(ActivitiesError):
    """Another live type in the project already uses this key (409)."""

    code = "activities/type-key-conflict"


class PayloadSchemaInvalid(ActivitiesError):
    """The registered ``payload_schema`` is not a well-formed JSON Schema (422)."""

    code = "activities/payload-schema-invalid"


class ValidatorConfigInvalid(ActivitiesError):
    """The validator configuration is invalid, e.g. an unregistered in-process
    ``validator_id`` (422)."""

    code = "activities/validator-config-invalid"


class SubmissionPayloadInvalid(ActivitiesError):
    """A submission payload violates its type's ``payload_schema`` (422)."""

    code = "activities/submission-payload-invalid"


__all__ = [
    "ActivitiesError",
    "ActivityActivationNotFound",
    "ActivityAlreadyActive",
    "ActivityNotActive",
    "ActivityTypeKeyConflict",
    "ActivityTypeNotFound",
    "PayloadSchemaInvalid",
    "SessionNotFound",
    "SubmissionNotFound",
    "SubmissionPayloadInvalid",
    "ValidatorConfigInvalid",
]
