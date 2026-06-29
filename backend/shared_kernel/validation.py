"""Reusable size guards for free-form JSON request fields (API-7).

`dict[str, Any]` request fields (agent configs, workflow definitions, trigger
payloads, provider configs) carry no implicit upper bound, so a single request
can persist a multi-megabyte — or pathologically deep — JSON blob straight to
the database. That is a cheap way to exhaust storage or to drive memory load on
every later read/serialize of the row.

`bounded_json` caps three independent dimensions before the value is accepted:

* serialized **byte size** — total storage footprint;
* **nesting depth** — guards against stack/parse amplification;
* **node count** — total scalars + containers, so width is bounded too.

The reusable `Bounded*` aliases below pin sensible limits per field shape; use
the `bounded_json` factory directly when a field needs its own bounds.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, TypeVar

from pydantic import AfterValidator

_T = TypeVar("_T")


def _measure(value: object) -> tuple[int, int]:
    """Return (max_depth, node_count) for a JSON-compatible structure.

    Iterative walk — recursion would itself blow the stack on a hostile input,
    which is exactly the shape we are trying to reject.
    """
    max_depth = 0
    nodes = 0
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        nodes += 1
        max_depth = max(depth, max_depth)
        if isinstance(node, dict):
            for v in node.values():
                stack.append((v, depth + 1))
        elif isinstance(node, (list | tuple)):
            for v in node:
                stack.append((v, depth + 1))
    return max_depth, nodes


def bounded_json(
    *,
    max_bytes: int,
    max_depth: int = 16,
    max_nodes: int = 2_000,
) -> AfterValidator:
    """Build a Pydantic validator that bounds the size of a JSON value.

    Attach via ``Annotated[dict[str, Any], bounded_json(max_bytes=...)]``. The
    checks run after Pydantic has parsed the value, so they apply equally to
    create and patch payloads.
    """

    def _check(value: _T) -> _T:
        if value is None:
            return value
        depth, nodes = _measure(value)
        if depth > max_depth:
            raise ValueError(f"JSON nesting too deep ({depth} > {max_depth} levels)")
        if nodes > max_nodes:
            raise ValueError(f"JSON has too many elements ({nodes} > {max_nodes})")
        try:
            encoded = json.dumps(value, separators=(",", ":"), default=str)
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            raise ValueError("value is not JSON-serializable") from exc
        size = len(encoded.encode("utf-8"))
        if size > max_bytes:
            raise ValueError(f"JSON payload too large ({size} > {max_bytes} bytes)")
        return value

    return AfterValidator(_check)


# Small key/value configuration blobs — agent wakeup/workflow capability config,
# RAG chunk params, search-provider config. Generous for legitimate settings,
# far below anything that stresses the row.
BoundedConfig = Annotated[dict[str, Any], bounded_json(max_bytes=16_000, max_depth=12, max_nodes=500)]

# Workflow graph definitions — legitimately large (many nodes/edges), so the
# byte/node ceiling is high while depth stays bounded against parse amplification.
BoundedDefinition = Annotated[dict[str, Any], bounded_json(max_bytes=512_000, max_depth=32, max_nodes=20_000)]

# Caller-supplied run trigger payloads — bounded but roomy enough for real
# initial-variable maps.
BoundedPayload = Annotated[dict[str, Any], bounded_json(max_bytes=64_000, max_depth=24, max_nodes=5_000)]
