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
