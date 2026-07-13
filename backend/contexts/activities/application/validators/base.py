"""Validator port + shared result-mapping helpers (activities-platform-core §5.3)."""

from __future__ import annotations

import json
from typing import Any, Protocol

from contexts.activities.domain.models import ActivityType, ValidationResult


class Validator(Protocol):
    """A validator produces a server-authoritative verdict for a payload."""

    async def validate(self, *, activity_type: ActivityType, payload: dict[str, Any]) -> ValidationResult: ...


class ValidatorUnavailable(Exception):
    """A remote validator could not produce a verdict (egress denied, non-2xx /
    non-zero exit, timeout, or unparseable output). The worker maps this to
    ``validation_status=error`` — distinct from a *wrong-answer* verdict."""


def result_from_json(raw: bytes | str) -> ValidationResult:
    """Map a remote validator's JSON envelope to a :class:`ValidationResult`.

    Expected shape: ``{"is_valid": bool, "error_class": str|null,
    "sub_scores": {..}, "detail": str|null}``. A missing/invalid ``is_valid`` or
    unparseable body raises :class:`ValidatorUnavailable` (never silently
    "valid")."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValidatorUnavailable("validator returned non-JSON output") from exc
    if not isinstance(data, dict) or not isinstance(data.get("is_valid"), bool):
        raise ValidatorUnavailable("validator output missing boolean 'is_valid'")
    sub_scores = data.get("sub_scores")
    error_class = data.get("error_class")
    detail = data.get("detail")
    return ValidationResult(
        is_valid=data["is_valid"],
        error_class=str(error_class) if error_class is not None else None,
        sub_scores=dict(sub_scores) if isinstance(sub_scores, dict) else {},
        detail=str(detail) if detail is not None else None,
    )


__all__ = ["Validator", "ValidatorUnavailable", "result_from_json"]
