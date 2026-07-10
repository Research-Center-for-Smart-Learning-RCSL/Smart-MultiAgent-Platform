"""Unit tests for the shared node/edge assembly helper extracted from
graphrag_graph_service.py and knowmap_graph_service.py (code review finding:
the two services duplicated this logic verbatim)."""

from __future__ import annotations

from dataclasses import dataclass

from contexts.knowledge.domain.graph_view_assembly import assemble_graph_view


@dataclass(frozen=True, slots=True)
class _Node:
    name: str
    degree: int
    build_id: str | None
    type: str


@dataclass(frozen=True, slots=True)
class _Edge:
    source: str
    relation: str
    target: str
    confidence: float


def test_assembles_nodes_and_edges() -> None:
    raw = {
        "nodes": [{"name": "alice", "degree": 2, "build_id": "b1", "type": "person"}],
        "edges": [{"subject": "alice", "relation": "knows", "object": "bob", "confidence": 0.9}],
        "truncated": True,
    }

    nodes, edges, truncated = assemble_graph_view(raw, node_cls=_Node, edge_cls=_Edge)

    assert truncated is True
    assert len(edges) == 1
    assert edges[0] == _Edge(source="alice", relation="knows", target="bob", confidence=0.9)
    names = {n.name for n in nodes}
    assert names == {"alice", "bob"}


def test_backfills_edge_endpoints_missing_from_node_query() -> None:
    # "bob" only appears as an edge endpoint — self-consistency fill-in must
    # still surface it as a zero-degree, typeless node.
    raw = {
        "nodes": [],
        "edges": [{"subject": "alice", "relation": "knows", "object": "bob", "confidence": 0.5}],
    }

    nodes, _edges, _truncated = assemble_graph_view(raw, node_cls=_Node, edge_cls=_Edge)

    by_name = {n.name: n for n in nodes}
    assert by_name["alice"] == _Node(name="alice", degree=0, build_id=None, type="")
    assert by_name["bob"] == _Node(name="bob", degree=0, build_id=None, type="")


def test_skips_edges_missing_subject_or_object() -> None:
    raw = {
        "nodes": [],
        "edges": [
            {"subject": "", "relation": "knows", "object": "bob", "confidence": 0.5},
            {"subject": "alice", "relation": "knows", "object": None, "confidence": 0.5},
        ],
    }

    nodes, edges, _truncated = assemble_graph_view(raw, node_cls=_Node, edge_cls=_Edge)

    assert edges == ()
    assert nodes == ()


def test_missing_nodes_and_edges_keys_default_to_empty() -> None:
    nodes, edges, truncated = assemble_graph_view({}, node_cls=_Node, edge_cls=_Edge)
    assert nodes == ()
    assert edges == ()
    assert truncated is False
