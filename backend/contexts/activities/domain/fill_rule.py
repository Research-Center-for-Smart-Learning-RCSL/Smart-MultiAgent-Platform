"""When one payload value counts as answered ([R30.27]).

Owned by the domain because two readers apply it: the ``filled_count``
validators in ``app/plugins`` and the creativity judge's fluency count, which
the activities context computes and cannot import ``app.plugins`` for.
"""

from __future__ import annotations

from typing import Any


def is_filled(value: Any) -> bool:
    """Whether one payload value counts as answered.

    ``False`` does **not** count. The generic schema form submits a boolean for
    every declared boolean property whether or not the participant touched it
    (``schemaFields.ts`` ``assemblePayload`` writes ``v === true`` unconditionally),
    so an unticked box is indistinguishable from an untouched one. Counting it
    would let a submission with nothing filled in at all score
    ``filled == len(properties)`` and pass any threshold — the metric would report
    the schema's size rather than the participant's effort.

    The cost is that a deliberate "no" is not counted as an answer. That is the
    right trade for a *completeness* measure.

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


def count_filled(payload: dict[str, Any], payload_schema: dict[str, Any]) -> int:
    """How many of the schema's declared properties ``payload`` answers.

    Declared properties only: JSON Schema admits additional keys unless a schema
    forbids them, so counting ``payload.values()`` would let padding inflate the
    count.
    """
    declared = payload_schema.get("properties") or {}
    return sum(1 for name in declared if is_filled(payload.get(name)))


__all__ = ["count_filled", "is_filled"]
