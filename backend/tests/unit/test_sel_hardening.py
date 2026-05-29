"""SEL save-time validation + lexer hardening (SEC-L5).

Pins three behaviours:
- a malformed numeric literal raises SELSyntaxError, not a raw ValueError;
- `validate()` parses + statically rejects non-whitelisted functions and
  syntax errors WITHOUT evaluating, so the linter can reject them at save time;
- the workflow linter's rule 15 flags bad SEL in condition / set_variable nodes.
"""

from __future__ import annotations

import pytest

from contexts.workflow.application.linter import rule_15_sel_expressions
from contexts.workflow.domain.errors import SELForbiddenConstruct, SELSyntaxError
from contexts.workflow.sel.evaluator import validate
from contexts.workflow.sel.lexer import tokenize


def test_malformed_number_raises_sel_syntax_error() -> None:
    with pytest.raises(SELSyntaxError):
        tokenize("1.2.3")


def test_validate_rejects_non_whitelisted_function() -> None:
    with pytest.raises(SELForbiddenConstruct):
        validate("evil(1)")


def test_validate_rejects_syntax_error() -> None:
    with pytest.raises(SELSyntaxError):
        validate("len(")


def test_validate_accepts_whitelisted_expression() -> None:
    # Must not raise — len/contains are whitelisted, syntax is valid.
    validate('len({{ x }}) > 0 and contains({{ y }}, "z")')


def test_rule_15_flags_bad_condition_when() -> None:
    defn = {
        "nodes": [
            {
                "id": "c1",
                "type": "condition",
                "config": {"branches": [{"when": "evil()", "port": "p"}]},
            }
        ]
    }
    issues = rule_15_sel_expressions(defn)
    assert any(i.rule == 15 and i.level == "error" and i.node_id == "c1" for i in issues)


def test_rule_15_flags_bad_set_variable_expression() -> None:
    defn = {
        "nodes": [
            {
                "id": "s1",
                "type": "set_variable",
                "config": {"assignments": [{"variable": "v", "expression": "1.2.3"}]},
            }
        ]
    }
    issues = rule_15_sel_expressions(defn)
    assert any(i.rule == 15 and i.node_id == "s1" for i in issues)


def test_rule_15_passes_valid_expression() -> None:
    defn = {
        "nodes": [
            {
                "id": "c1",
                "type": "condition",
                "config": {"branches": [{"when": "{{ x }} == 1", "port": "p"}]},
            }
        ]
    }
    assert rule_15_sel_expressions(defn) == []
