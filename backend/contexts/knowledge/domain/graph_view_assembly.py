"""Shared node/edge assembly for the GraphRAG and Knowledge Map graph
read-models (E.7 / R11.24) — framework-free, like the rest of `domain/`.

`graphrag_graph_service.py` and `knowmap_graph_service.py` bound, parse, and
self-consistency-backfill a Neo4j `fetch_graph()` result identically; the
only difference between them is which dataclass type each wraps a row in.
This function takes those types as parameters instead of the row-assembly
logic being duplicated per caller.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

N_co = TypeVar("N_co", covariant=True)
E_co = TypeVar("E_co", covariant=True)


class _NodeCtor(Protocol[N_co]):
    # `type` (not `kind`) to match GraphNode/KnowmapGraphNode's actual field name.
    def __call__(self, *, name: str, degree: int, build_id: str | None, type: str) -> N_co: ...  # noqa: A002


class _EdgeCtor(Protocol[E_co]):
    def __call__(self, *, source: str, relation: str, target: str, confidence: float) -> E_co: ...


def assemble_graph_view(
    raw: dict[str, Any],
    *,
    node_cls: _NodeCtor[N_co],
    edge_cls: _EdgeCtor[E_co],
) -> tuple[tuple[N_co, ...], tuple[E_co, ...], bool]:
    """Parse a Neo4jAsyncDriver.fetch_graph() result into bounded node/edge
    dataclasses. Returns ``(nodes, edges, truncated)``.

    Keeps the view self-consistent: an edge endpoint that falls outside the
    degree-capped node window still gets a minimal node so every edge has
    both endpoints present.
    """
    nodes: dict[str, N_co] = {}
    for row in raw.get("nodes") or []:
        name = str(row.get("name") or "")
        if not name:
            continue
        b_raw = row.get("build_id")
        nodes[name] = node_cls(
            name=name,
            degree=int(row.get("degree") or 0),
            build_id=str(b_raw) if b_raw else None,
            type=str(row.get("type") or ""),
        )

    edges: list[E_co] = []
    for row in raw.get("edges") or []:
        source = str(row.get("subject") or "")
        target = str(row.get("object") or "")
        if not source or not target:
            continue
        edges.append(
            edge_cls(
                source=source,
                relation=str(row.get("relation") or ""),
                target=target,
                confidence=float(row.get("confidence") or 0.0),
            )
        )
        for endpoint in (source, target):
            if endpoint not in nodes:
                nodes[endpoint] = node_cls(name=endpoint, degree=0, build_id=None, type="")

    return tuple(nodes.values()), tuple(edges), bool(raw.get("truncated"))


__all__ = ["assemble_graph_view"]
