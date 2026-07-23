"""Trigger-level loop guard for the a2a_event self-amplification defect (F-4).

Part 1 (this file's linter tests): a workflow whose ``a2a_event`` trigger targets
the same agent one of its own nodes sends a matching envelope to forms a closed
causal cycle that nothing else breaks. Lint rule 17 rejects that shape at save
time. It compares the *emitted* message type against the trigger's
``event_types`` so a disjoint pair (trigger on ``notify``, node emits ``call``)
stays legal.

Part 3 (the run_triggered_workflow budget tests): the shared trigger-start path
enforces a per-workflow rolling-window ceiling so a missed provenance path still
cannot spend without bound. See docs/tasks/2026-07-22-a2a-event-trigger-loop-guard.
"""

from __future__ import annotations

from typing import Any

from contexts.workflow.application.linter import validate_definition

_A = "11111111-1111-1111-1111-111111111111"
_B = "22222222-2222-2222-2222-222222222222"


def _self_trigger_defn(
    *,
    trigger_agent: str,
    event_types: list[str],
    invoke_agent: str,
) -> dict[str, Any]:
    """Minimal definition that passes rules 1-16: trigger -> agent_invocation ->
    end, with both agent_invocation ports connected."""
    return {
        "schema_version": "1.0",
        "name": "self-trigger",
        "entry_node_id": "t1",
        "nodes": [
            {
                "id": "t1",
                "type": "trigger",
                "config": {
                    "trigger_type": "a2a_event",
                    "agent_id": trigger_agent,
                    "event_types": event_types,
                },
            },
            {
                "id": "a1",
                "type": "agent_invocation",
                "config": {"agent_id": invoke_agent, "input_template": "hi"},
            },
            {"id": "e1", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "ed1", "from": "t1", "to": "a1", "from_port": "default"},
            {"id": "ed2", "from": "a1", "to": "e1", "from_port": "success"},
            {"id": "ed3", "from": "a1", "to": "e1", "from_port": "failure"},
        ],
    }


def _instruct_trigger_defn(
    *,
    trigger_agent: str,
    event_types: list[str],
    issuer_agent: str,
    target_agent: str,
) -> dict[str, Any]:
    """Instruct-edged variant: trigger -> instruct -> end."""
    return {
        "schema_version": "1.0",
        "name": "instruct-self-trigger",
        "entry_node_id": "t1",
        "nodes": [
            {
                "id": "t1",
                "type": "trigger",
                "config": {
                    "trigger_type": "a2a_event",
                    "agent_id": trigger_agent,
                    "event_types": event_types,
                },
            },
            {
                "id": "i1",
                "type": "instruct",
                "config": {
                    "issuer_agent_id": issuer_agent,
                    "target_agent_id": target_agent,
                    "instruction_template": "do x",
                },
            },
            {"id": "e1", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "ed1", "from": "t1", "to": "i1", "from_port": "default"},
            {"id": "ed2", "from": "i1", "to": "e1", "from_port": "success"},
            {"id": "ed3", "from": "i1", "to": "e1", "from_port": "failure"},
        ],
    }


class TestRule17SelfTrigger:
    def test_a2a_event_trigger_may_not_target_an_agent_the_workflow_invokes(self) -> None:
        # T-1: trigger on A + agent_invocation on A emitting `call`, and the
        # trigger listens for `call` — a closed self-amplifying cycle.
        defn = _self_trigger_defn(trigger_agent=_A, event_types=["call"], invoke_agent=_A)
        result = validate_definition(defn, valid_agent_ids=frozenset({_A}))
        assert result.valid is False
        assert any(e.rule == 17 for e in result.errors)

    def test_lint_allows_a_trigger_whose_event_types_exclude_the_emitted_type(self) -> None:
        # T-2: the false-positive floor. agent_invocation emits `call`, but the
        # trigger only listens for `notify` — no cycle, must stay legal.
        defn = _self_trigger_defn(trigger_agent=_A, event_types=["notify"], invoke_agent=_A)
        result = validate_definition(defn, valid_agent_ids=frozenset({_A}))
        assert not any(e.rule == 17 for e in result.errors)
        assert result.valid is True

    def test_lint_allows_invocation_of_a_different_agent(self) -> None:
        # A trigger on A whose node invokes B is not a self-cycle.
        defn = _self_trigger_defn(trigger_agent=_A, event_types=["call"], invoke_agent=_B)
        result = validate_definition(defn, valid_agent_ids=frozenset({_A, _B}))
        assert not any(e.rule == 17 for e in result.errors)
        assert result.valid is True

    def test_lint_rejects_the_instruct_edged_cycle(self) -> None:
        # T-3: the sibling that goes live when F-9 (a2a-scope-context-wiring)
        # lands. instruct emits `instruct` to B; trigger on B listens for it.
        defn = _instruct_trigger_defn(
            trigger_agent=_B, event_types=["instruct"], issuer_agent=_A, target_agent=_B
        )
        result = validate_definition(defn, valid_agent_ids=frozenset({_A, _B}))
        assert result.valid is False
        assert any(e.rule == 17 for e in result.errors)

    def test_lint_allows_instruct_when_event_types_exclude_instruct(self) -> None:
        # Instruct emits `instruct`; a trigger on B listening only for `call`
        # does not form a cycle.
        defn = _instruct_trigger_defn(
            trigger_agent=_B, event_types=["call"], issuer_agent=_A, target_agent=_B
        )
        result = validate_definition(defn, valid_agent_ids=frozenset({_A, _B}))
        assert not any(e.rule == 17 for e in result.errors)
        assert result.valid is True
