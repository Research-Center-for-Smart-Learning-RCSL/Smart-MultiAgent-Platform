"""The order of a payload schema's declared properties.

``payload_schema`` is stored as jsonb, which keeps object keys sorted by length
and then bytes, not as the author wrote them
(``tests/integration/test_activity_schema_key_order.py``). ``x-order`` is how an
author fixes the order; properties without one follow those that have one, in
the order the schema returns them.
"""

from __future__ import annotations

from typing import Any


def ordered_properties(payload_schema: Any) -> list[tuple[str, Any]]:
    """``[(property name, property schema)]`` in declared order; ``[]`` without properties."""
    properties = payload_schema.get("properties") if isinstance(payload_schema, dict) else None
    if not isinstance(properties, dict):
        return []

    def sort_key(item: tuple[int, tuple[str, Any]]) -> tuple[int, float, int]:
        index, (_, spec) = item
        order = spec.get("x-order") if isinstance(spec, dict) else None
        if isinstance(order, bool) or not isinstance(order, int | float):
            return (1, 0.0, index)
        return (0, float(order), index)

    return [prop for _, prop in sorted(enumerate(properties.items()), key=sort_key)]


__all__ = ["ordered_properties"]
