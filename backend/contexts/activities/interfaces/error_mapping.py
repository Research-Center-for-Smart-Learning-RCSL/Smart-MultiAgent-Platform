"""Activities domain errors → RFC 7807 registration (Chapter §30).

Dispatch + 500 fallback live in ``shared_kernel.errors.context_handler``.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from contexts.activities.domain import errors
from shared_kernel.errors.context_handler import ErrorMap, register_context_handler

_MAP: ErrorMap = {
    errors.ActivityTypeNotFound: (
        "activities/type-not-found",
        404,
        "Activity type not found",
    ),
    errors.SessionNotFound: (
        "activities/session-not-found",
        404,
        "Activity session not found",
    ),
    errors.ActivityActivationNotFound: (
        "activities/activation-not-found",
        404,
        "Activity activation not found",
    ),
    errors.ActivityAlreadyActive: (
        "activities/already-active",
        409,
        "A different activity is already active in this chatroom",
    ),
    errors.ActivityNotActive: (
        "activities/not-active",
        409,
        "This activity is not active in this chatroom",
    ),
    errors.SubmissionNotFound: (
        "activities/submission-not-found",
        404,
        "Activity submission not found",
    ),
    errors.ActivityTypeKeyConflict: (
        "activities/type-key-conflict",
        409,
        "An activity type with this key already exists in the project",
    ),
    errors.ActivityTypeActive: (
        "activities/type-active",
        409,
        "This activity type is active in a room; its schema or validator cannot be edited",
    ),
    errors.PayloadSchemaInvalid: (
        "activities/payload-schema-invalid",
        422,
        "The payload schema is not a well-formed JSON Schema",
    ),
    errors.ValidatorConfigInvalid: (
        "activities/validator-config-invalid",
        422,
        "The validator configuration is invalid",
    ),
    errors.SubmissionPayloadInvalid: (
        "activities/submission-payload-invalid",
        422,
        "The submission payload violates the activity type schema",
    ),
    # 409 rather than 422: the request is well-formed and was legal when the type
    # was authored — what conflicts is the platform policy's current state.
    errors.ActivityTypeViolatesPolicy: (
        "activities/type-violates-policy",
        409,
        "This activity type conflicts with the platform activity policy",
    ),
    errors.ActivityPolicyVersionMismatch: (
        "activities/policy-version-mismatch",
        409,
        "The activity policy changed since this form was loaded",
    ),
    errors.ActivityPolicyInconsistent: (
        "activities/policy-inconsistent",
        422,
        "The activity policy contradicts itself",
    ),
    errors.PlatformActivityTypeReadOnly: (
        "activities/platform-type-read-only",
        403,
        "This activity type is owned by the platform and cannot be changed from a project",
    ),
    errors.ActivityTypeNotOptedIn: (
        "activities/type-not-opted-in",
        404,
        "This project has not enabled that platform activity type",
    ),
}


def _extras(exc: Exception) -> dict[str, Any]:
    """Structured problem members beyond title/detail.

    ``field`` is what lets the client render a translated refusal naming the
    offending switch; ``detail`` alone carries it only as untranslated English
    prose ([R30.30] requires the facilitator be told what is wrong).
    """
    extras: dict[str, Any] = {}
    if isinstance(exc, errors.ActivityTypeViolatesPolicy):
        extras["field"] = exc.field
    return extras


def register(app: FastAPI) -> None:
    register_context_handler(app, errors.ActivitiesError, _MAP, _extras)


__all__ = ["register"]
