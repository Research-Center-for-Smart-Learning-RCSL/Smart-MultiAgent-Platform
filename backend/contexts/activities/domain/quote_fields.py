"""Quoted submission fields: the few values a type lets agents read verbatim ([R30.15] amended).

A computed digest says which fields were answered, never what was written. Some
courses need one short answer back (the CRAT target character, so the teacher
agent can check it against the bank). ``validator_config.quote_fields`` names up
to three declared string properties whose values ride on the agent's activity row.

Here rather than beside the validator because two layers read it: the plugin that
validates the config, and the repository that projects the values, which may not
import ``app.plugins``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

QUOTE_FIELDS_KEY: Final = "quote_fields"
#: The one validator that checks ``quote_fields`` against the payload schema. Any
#: other validator accepts the key unchecked, so its names are never quoted.
QUOTING_VALIDATOR_ID: Final = "filled_count_coverage"
MAX_QUOTE_FIELDS: Final = 3
QUOTE_VALUE_MAX: Final = 40
QUOTE_TRUNCATED_SUFFIX: Final = " (truncated)"


def quote_field_errors(config: Mapping[str, Any], payload_schema: Mapping[str, Any] | None) -> str | None:
    """Why ``config``'s ``quote_fields`` is unusable, or ``None`` when it is fine or absent.

    ``payload_schema`` of ``None`` checks the config's own shape only, for the
    config-only hook; the schema hook passes the schema to check the names.
    """
    if QUOTE_FIELDS_KEY not in config:
        return None
    names = config[QUOTE_FIELDS_KEY]
    if not isinstance(names, list):
        return "quote_fields must be a list of property names"
    if len(names) > MAX_QUOTE_FIELDS:
        return f"quote_fields names at most {MAX_QUOTE_FIELDS} properties"
    if not all(isinstance(n, str) and n for n in names):
        return "quote_fields entries must be non-empty strings"
    if len(set(names)) != len(names):
        return "quote_fields must not repeat a property"
    if payload_schema is None:
        return None
    declared = payload_schema.get("properties")
    declared = declared if isinstance(declared, Mapping) else {}
    for name in names:
        prop = declared.get(name)
        if not isinstance(prop, Mapping):
            return f"quote_fields names {name!r}, which the payload schema does not declare"
        if prop.get("type") != "string":
            return f"quote_fields names {name!r}, which is not a string property"
    return None


def _cap(value: str) -> str:
    one_line = " ".join(value.split())
    if len(one_line) <= QUOTE_VALUE_MAX:
        return one_line
    return one_line[:QUOTE_VALUE_MAX] + QUOTE_TRUNCATED_SUFFIX


def quoted_values(payload: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """The ``(name, value)`` pairs to quote, in declared order, unfilled ones skipped.

    Defensive about a config that predates validation or a payload that drifted:
    anything that is not a usable list or a string value is skipped, never raised.
    """
    if config.get("validator_id") != QUOTING_VALIDATOR_ID:
        return ()
    names = config.get(QUOTE_FIELDS_KEY)
    if not isinstance(names, list):
        return ()
    out: list[tuple[str, str]] = []
    for name in names[:MAX_QUOTE_FIELDS]:
        value = payload.get(name) if isinstance(name, str) else None
        if isinstance(value, str) and value.strip():
            out.append((name, _cap(value)))
    return tuple(out)


__all__ = [
    "MAX_QUOTE_FIELDS",
    "QUOTE_FIELDS_KEY",
    "QUOTE_TRUNCATED_SUFFIX",
    "QUOTE_VALUE_MAX",
    "QUOTING_VALIDATOR_ID",
    "quote_field_errors",
    "quoted_values",
]
