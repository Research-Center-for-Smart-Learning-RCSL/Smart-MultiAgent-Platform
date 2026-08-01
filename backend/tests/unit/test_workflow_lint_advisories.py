"""Advisory-warning coverage for the timer wait and join timeout non-capabilities (F-2, F-36).

C-2 scopes W3 ("wait_for_event has no timeout edge") away from timer waits — a timer
has no producer for its timeout port at all, so an absent edge there is correct, not a
gap — and adds a new advisory when a timer wait's timeout port IS wired, since that
edge can never fire. C-3 adds the equivalent advisory for a join's timeout port, which
has no producer either (Q-2: recorded as a non-capability rather than built).
"""

from __future__ import annotations

from contexts.workflow.application.linter import validate_definition

_TRIGGER = {"id": "t1", "type": "trigger", "config": {"trigger_type": "manual"}}


def _defn_with_wait(*, event_type: str, wire_timeout: bool) -> dict:
    edges = [
        {"id": "x1", "from": "t1", "to": "w1", "from_port": "default"},
        {"id": "x2", "from": "w1", "to": "e1", "from_port": "default"},
    ]
    if wire_timeout:
        edges.append({"id": "x3", "from": "w1", "to": "e1", "from_port": "timeout"})
    return {
        "entry_node_id": "t1",
        "nodes": [
            _TRIGGER,
            {
                "id": "w1",
                "type": "wait_for_event",
                "config": {"event_type": event_type, "timeout_seconds": 300, "delay_seconds": 60},
            },
            {"id": "e1", "type": "end", "config": {"status": "success"}},
        ],
        "edges": edges,
    }


def _defn_with_join(*, wire_timeout: bool) -> dict:
    edges = [
        {"id": "x1", "from": "t1", "to": "p1", "from_port": "default"},
        {"id": "x2", "from": "p1", "to": "a1", "from_port": "default"},
        {"id": "x3", "from": "p1", "to": "b1", "from_port": "default"},
        {"id": "x4", "from": "a1", "to": "j1", "from_port": "default"},
        {"id": "x5", "from": "b1", "to": "j1", "from_port": "default"},
        {"id": "x6", "from": "j1", "to": "e1", "from_port": "default"},
    ]
    if wire_timeout:
        edges.append({"id": "x7", "from": "j1", "to": "e2", "from_port": "timeout"})
    return {
        "entry_node_id": "t1",
        "nodes": [
            _TRIGGER,
            {"id": "p1", "type": "parallel", "config": {}},
            {
                "id": "a1",
                "type": "set_variable",
                "config": {"assignments": [{"variable": "x", "value": "1"}]},
            },
            {
                "id": "b1",
                "type": "set_variable",
                "config": {"assignments": [{"variable": "y", "value": "2"}]},
            },
            {"id": "j1", "type": "join", "config": {"mode": "all"}},
            {"id": "e1", "type": "end", "config": {"status": "success"}},
            {"id": "e2", "type": "end", "config": {"status": "failure"}},
        ],
        "edges": edges,
    }


def test_timer_wait_does_not_warn_about_missing_timeout_edge() -> None:
    result = validate_definition(_defn_with_wait(event_type="timer", wire_timeout=False))

    assert not any("no timeout edge" in w.message for w in result.warnings)


def test_timer_wait_with_timeout_edge_warns_unreachable() -> None:
    result = validate_definition(_defn_with_wait(event_type="timer", wire_timeout=True))

    matches = [w for w in result.warnings if "timer wait's timeout port" in w.message]
    assert len(matches) == 1
    assert result.valid is True


def test_join_timeout_edge_warns_not_implemented() -> None:
    result = validate_definition(_defn_with_join(wire_timeout=True))

    matches = [w for w in result.warnings if "join timeout is not implemented" in w.message]
    assert len(matches) == 1
    assert result.valid is True


def test_join_timeout_edge_still_saves() -> None:
    result = validate_definition(_defn_with_join(wire_timeout=True))

    assert result.errors == []


def test_message_wait_without_timeout_edge_still_warns() -> None:
    result = validate_definition(_defn_with_wait(event_type="message_in_room", wire_timeout=False))

    assert any("no timeout edge" in w.message for w in result.warnings)
