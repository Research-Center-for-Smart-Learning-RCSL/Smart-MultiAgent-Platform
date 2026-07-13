"""JSON Schema helpers for activity types (mirrors workflow_service.py:439-445).

Two concerns kept separate: is a *registered schema* itself well-formed
(checked once, at type registration), and does a *submission payload* satisfy a
type's schema (checked on every submit before persistence or dispatch).
"""

from __future__ import annotations

from typing import Any

import jsonschema
from jsonschema import Draft202012Validator

from contexts.activities.domain.errors import PayloadSchemaInvalid


def validate_schema_wellformed(schema: dict[str, Any]) -> None:
    """Raise :class:`PayloadSchemaInvalid` if ``schema`` is not a well-formed
    JSON Schema (Draft 2020-12)."""
    try:
        Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise PayloadSchemaInvalid(str(exc.message)) from exc


def payload_errors(schema: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    """Return all schema-violation messages for ``payload`` (empty = valid)."""
    validator = Draft202012Validator(schema)
    return [err.message for err in validator.iter_errors(payload)]


__all__ = ["payload_errors", "validate_schema_wellformed"]
