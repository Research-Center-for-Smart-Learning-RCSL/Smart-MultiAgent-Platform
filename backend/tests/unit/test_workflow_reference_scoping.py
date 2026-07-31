"""Workflow linter scopes agent/chatroom references to the provided id sets (F1).

The router used to call WorkflowService.create/patch WITHOUT valid_agent_ids /
valid_chatroom_ids, so the linter saw empty sets and rejected *every* workflow
that referenced an agent (rule 6) or chatroom (rule 8) — i.e. any non-trivial
workflow failed to save. These pin the contract the router fix relies on:
a reference is accepted iff its id is in the supplied (project-scoped) set.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from contexts.workflow.application.linter import validate_definition
from contexts.workflow.application.workflow_service import WorkflowService
from contexts.workflow.domain.errors import WorkflowValidationFailed

_AGENT_ID = "11111111-1111-1111-1111-111111111111"
_ROOM_ID = "22222222-2222-2222-2222-222222222222"
_ISSUER_ID = "44444444-4444-4444-4444-444444444444"
_TARGET_ID = "55555555-5555-5555-5555-555555555555"
_LEADER_ID = "66666666-6666-6666-6666-666666666666"
_APPROVER_ID = "77777777-7777-7777-7777-777777777777"


def _defn_with_agent() -> dict:
    return {
        "nodes": [
            {"id": "a1", "type": "agent_invocation", "config": {"agent_id": _AGENT_ID}},
        ],
        "edges": [],
    }


def _defn_with_chatroom() -> dict:
    return {
        "nodes": [
            {"id": "i1", "type": "instruct", "config": {"chatroom_id": _ROOM_ID}},
        ],
        "edges": [],
    }


def test_agent_reference_rejected_when_out_of_scope() -> None:
    # Reproduces the F1 symptom: empty valid set ⇒ rule-6 rejection.
    result = validate_definition(_defn_with_agent())
    assert any(e.rule == 6 for e in result.errors)


def test_agent_reference_accepted_when_in_scope() -> None:
    result = validate_definition(_defn_with_agent(), valid_agent_ids=frozenset({_AGENT_ID}))
    assert not any(e.rule == 6 for e in result.errors)


def test_cross_tenant_agent_still_rejected() -> None:
    # A different project's agent id is NOT in scope ⇒ still rejected.
    result = validate_definition(_defn_with_agent(), valid_agent_ids=frozenset({_ROOM_ID}))
    assert any(e.rule == 6 for e in result.errors)


def test_chatroom_reference_rejected_when_out_of_scope() -> None:
    result = validate_definition(_defn_with_chatroom())
    assert any(e.rule == 8 for e in result.errors)


def test_chatroom_reference_accepted_when_in_scope() -> None:
    result = validate_definition(_defn_with_chatroom(), valid_chatroom_ids=frozenset({_ROOM_ID}))
    assert not any(e.rule == 8 for e in result.errors)


def _defn_with_subagent_spawn(*, include_spawn: bool = True) -> dict:
    """trigger -> subagent_spawn -> end, with the failure port wired (rule 13).

    ``include_spawn=False`` is the same workflow with the node removed — the edit an
    author must still be able to save (R2).
    """
    nodes: list[dict] = [
        {"id": "t1", "type": "trigger", "config": {"trigger_type": "manual"}},
        {"id": "e1", "type": "end", "config": {"status": "success"}},
    ]
    edges: list[dict] = []
    if include_spawn:
        nodes.insert(
            1,
            {
                "id": "s1",
                "type": "subagent_spawn",
                "config": {"parent_agent_id": _AGENT_ID, "task_template": "do it"},
            },
        )
        edges = [
            {"id": "x1", "from": "t1", "to": "s1", "from_port": "default"},
            {"id": "x2", "from": "s1", "to": "e1", "from_port": "success"},
            {"id": "x3", "from": "s1", "to": "e1", "from_port": "failure"},
        ]
    else:
        edges = [{"id": "x1", "from": "t1", "to": "e1", "from_port": "default"}]
    return {"entry_node_id": "t1", "nodes": nodes, "edges": edges}


def test_subagent_spawn_emits_advisory_warning() -> None:
    result = validate_definition(
        _defn_with_subagent_spawn(),
        valid_agent_ids=frozenset({_AGENT_ID}),
    )

    assert any("subagent_spawn is not implemented" in w.message for w in result.warnings)
    # Advisory, not blocking: the definition must still save.
    assert result.valid is True
    assert result.errors == []


def test_removing_subagent_spawn_still_validates() -> None:
    # R2: create and patch share one validator, so a blocking rule would reject the
    # very edit that removes the node. This pins that the escape hatch stays open.
    result = validate_definition(
        _defn_with_subagent_spawn(include_spawn=False),
        valid_agent_ids=frozenset({_AGENT_ID}),
    )

    assert result.valid is True
    assert not any("subagent_spawn" in w.message for w in result.warnings)


def _defn_with_instruct() -> dict:
    return {
        "entry_node_id": "t1",
        "nodes": [
            {"id": "t1", "type": "trigger", "config": {"trigger_type": "manual"}},
            {
                "id": "i1",
                "type": "instruct",
                "config": {
                    "issuer_agent_id": _ISSUER_ID,
                    "target_agent_id": _TARGET_ID,
                    "instruction_template": "do it",
                },
            },
            {"id": "e1", "type": "end", "config": {"status": "success"}},
            {"id": "e2", "type": "end", "config": {"status": "failure"}},
        ],
        "edges": [
            {"id": "x1", "from": "t1", "to": "i1", "from_port": "default"},
            {"id": "x2", "from": "i1", "to": "e1", "from_port": "success"},
            {"id": "x3", "from": "i1", "to": "e2", "from_port": "failure"},
        ],
    }


def test_incapable_issuer_produces_advisory_warning_not_error() -> None:
    """T-9 (instruct): an issuer lacking can_instruct warns, but the definition
    still saves — the runtime gate is InstructService.issue, not the linter (Q-5)."""
    result = validate_definition(
        _defn_with_instruct(),
        valid_agent_ids=frozenset({_ISSUER_ID, _TARGET_ID}),
        can_instruct_agent_ids=frozenset(),
    )

    assert any("can_instruct" in w.message for w in result.warnings)
    assert result.valid is True
    assert result.errors == []


def test_capable_issuer_produces_no_advisory_warning() -> None:
    result = validate_definition(
        _defn_with_instruct(),
        valid_agent_ids=frozenset({_ISSUER_ID, _TARGET_ID}),
        can_instruct_agent_ids=frozenset({_ISSUER_ID}),
    )

    assert not any("can_instruct" in w.message for w in result.warnings)


def _defn_with_approval_gate() -> dict:
    return {
        "entry_node_id": "t1",
        "nodes": [
            {"id": "t1", "type": "trigger", "config": {"trigger_type": "manual"}},
            {
                "id": "g1",
                "type": "approval_gate",
                "config": {
                    "mode": "single",
                    "leader_agent_id": _LEADER_ID,
                    "approvers": [_LEADER_ID, _APPROVER_ID],
                    "timeout_seconds": 60,
                    "question_template": "Approve?",
                },
            },
            {"id": "e1", "type": "end", "config": {"status": "success"}},
            {"id": "e2", "type": "end", "config": {"status": "failure"}},
            {"id": "e3", "type": "end", "config": {"status": "success"}},
        ],
        "edges": [
            {"id": "x1", "from": "t1", "to": "g1", "from_port": "default"},
            {"id": "x2", "from": "g1", "to": "e1", "from_port": "approved"},
            {"id": "x3", "from": "g1", "to": "e2", "from_port": "rejected"},
            {"id": "x4", "from": "g1", "to": "e3", "from_port": "timeout"},
        ],
    }


def test_incapable_approver_and_leader_each_produce_advisory_warning() -> None:
    """T-9 (approval): the leader is folded into approvers by the executor at
    run time, so a leader-only-ineligible must warn too, and the definition
    still saves — the runtime gate is ApprovalService.create_gate (Q-5)."""
    result = validate_definition(
        _defn_with_approval_gate(),
        valid_agent_ids=frozenset({_LEADER_ID, _APPROVER_ID}),
        can_approve_agent_ids=frozenset(),
    )

    messages = [w.message for w in result.warnings]
    assert any(_LEADER_ID in m and "can_approve" in m for m in messages)
    assert any(_APPROVER_ID in m and "can_approve" in m for m in messages)
    assert result.valid is True
    assert result.errors == []


def test_capable_approvers_produce_no_advisory_warning() -> None:
    result = validate_definition(
        _defn_with_approval_gate(),
        valid_agent_ids=frozenset({_LEADER_ID, _APPROVER_ID}),
        can_approve_agent_ids=frozenset({_LEADER_ID, _APPROVER_ID}),
    )

    assert not any("can_approve" in w.message for w in result.warnings)


def test_incapable_subagent_parent_produces_advisory_warning() -> None:
    """T-9 (subagent): parent_agent_id lacking can_create_subagent warns; still
    save-time advisory only (Q-2/Q-3 — no runtime gate exists to enforce it)."""
    result = validate_definition(
        _defn_with_subagent_spawn(),
        valid_agent_ids=frozenset({_AGENT_ID}),
        can_create_subagent_agent_ids=frozenset(),
    )

    assert any("can_create_subagent" in w.message for w in result.warnings)
    assert result.valid is True
    assert result.errors == []


def test_capable_subagent_parent_produces_no_advisory_warning() -> None:
    result = validate_definition(
        _defn_with_subagent_spawn(),
        valid_agent_ids=frozenset({_AGENT_ID}),
        can_create_subagent_agent_ids=frozenset({_AGENT_ID}),
    )

    assert not any("can_create_subagent" in w.message for w in result.warnings)


def test_approval_gate_config_rejects_chatroom_id() -> None:
    definition = {
        "schema_version": "1.0",
        "name": "approval scope pin",
        "entry_node_id": "gate1",
        "nodes": [
            {
                "id": "gate1",
                "type": "approval_gate",
                "position": {"x": 0, "y": 0},
                "config": {
                    "mode": "single",
                    "leader_agent_id": "33333333-3333-3333-3333-333333333333",
                    "approvers": ["33333333-3333-3333-3333-333333333333"],
                    "timeout_seconds": 60,
                    "question_template": "Approve?",
                    "chatroom_id": _ROOM_ID,
                },
            }
        ],
        "edges": [],
    }

    with pytest.raises(WorkflowValidationFailed):
        WorkflowService(MagicMock())._validate_schema(definition)
