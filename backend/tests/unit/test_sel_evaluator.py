"""Comprehensive tests for the SEL v1 evaluator and template engine.

Covers: evaluate(), _call_func dispatch (all 16 functions), _resolve_var
scope routing, arithmetic/comparison/boolean operators, truthiness, type
coercion, budget enforcement, and template interpolation with nested paths.
"""

from __future__ import annotations

import pytest

from contexts.workflow.domain.errors import (
    SELBudgetExceeded,
    SELForbiddenConstruct,
    SELSyntaxError,
)
from contexts.workflow.sel.evaluator import evaluate, validate
from contexts.workflow.sel.template import interpolate

# ---------------------------------------------------------------------------
# Literals + variable resolution
# ---------------------------------------------------------------------------


class TestLiterals:
    def test_integer(self) -> None:
        assert evaluate("42") == 42

    def test_negative_integer(self) -> None:
        assert evaluate("-7") == -7

    def test_float(self) -> None:
        assert evaluate("3.14") == pytest.approx(3.14)

    def test_string(self) -> None:
        assert evaluate('"hello"') == "hello"

    def test_bool_true(self) -> None:
        assert evaluate("true") is True

    def test_bool_false(self) -> None:
        assert evaluate("false") is False

    def test_null(self) -> None:
        assert evaluate("null") is None


class TestVarResolution:
    def test_simple_var(self) -> None:
        assert evaluate("{{ x }}", {"x": 10}) == 10

    def test_dotted_path(self) -> None:
        assert evaluate("{{ a.b.c }}", {"a": {"b": {"c": 99}}}) == 99

    def test_undeclared_var_is_none(self) -> None:
        assert evaluate("{{ missing }}") is None

    def test_trigger_scope(self) -> None:
        assert evaluate("{{ trigger.event }}", {"__trigger__": {"event": "click"}}) == "click"

    def test_ctx_scope(self) -> None:
        assert evaluate("{{ ctx.run_id }}", {"__ctx__": {"run_id": "abc"}}) == "abc"

    def test_index_into_list(self) -> None:
        assert evaluate("{{ items[0] }}", {"items": [10, 20, 30]}) == 10

    def test_index_out_of_bounds(self) -> None:
        assert evaluate("{{ items[99] }}", {"items": [1]}) is None

    def test_nested_dict_then_list(self) -> None:
        assert evaluate("{{ a.b[1] }}", {"a": {"b": ["x", "y"]}}) == "y"

    def test_none_intermediate(self) -> None:
        assert evaluate("{{ a.b.c }}", {"a": {"b": None}}) is None


# ---------------------------------------------------------------------------
# Arithmetic operators
# ---------------------------------------------------------------------------


class TestArithmetic:
    def test_add_numbers(self) -> None:
        assert evaluate("2 + 3") == 5

    def test_add_strings(self) -> None:
        assert evaluate('"foo" + "bar"') == "foobar"

    def test_add_mismatched_types(self) -> None:
        assert evaluate('"a" + 1') == 0

    def test_subtract(self) -> None:
        assert evaluate("10 - 3") == 7

    def test_multiply(self) -> None:
        assert evaluate("4 * 5") == 20

    def test_divide(self) -> None:
        assert evaluate("10 / 4") == pytest.approx(2.5)

    def test_divide_by_zero(self) -> None:
        assert evaluate("1 / 0") == 0

    def test_modulo(self) -> None:
        assert evaluate("7 % 3") == 1

    def test_modulo_by_zero(self) -> None:
        assert evaluate("7 % 0") == 0

    def test_arithmetic_on_non_numbers(self) -> None:
        assert evaluate('"a" - "b"') == 0


# ---------------------------------------------------------------------------
# Comparison operators
# ---------------------------------------------------------------------------


class TestComparison:
    def test_eq_same_type(self) -> None:
        assert evaluate("1 == 1") is True

    def test_eq_different_values(self) -> None:
        assert evaluate("1 == 2") is False

    def test_eq_int_float(self) -> None:
        assert evaluate("1 == 1.0") is True

    def test_eq_null_null(self) -> None:
        assert evaluate("null == null") is True

    def test_eq_null_non_null(self) -> None:
        assert evaluate("null == 0") is False

    def test_neq(self) -> None:
        assert evaluate("1 != 2") is True

    def test_lt(self) -> None:
        assert evaluate("1 < 2") is True

    def test_lte(self) -> None:
        assert evaluate("2 <= 2") is True

    def test_gt(self) -> None:
        assert evaluate("3 > 2") is True

    def test_gte(self) -> None:
        assert evaluate("2 >= 3") is False

    def test_comparison_mismatched_types(self) -> None:
        assert evaluate('"a" < 1') is False

    def test_eq_mismatched_types_string_int(self) -> None:
        assert evaluate('"1" == 1') is False


# ---------------------------------------------------------------------------
# Boolean operators
# ---------------------------------------------------------------------------


class TestBooleanOps:
    def test_and_both_true(self) -> None:
        assert evaluate("true and true") is True

    def test_and_short_circuit(self) -> None:
        assert evaluate("false and true") is False

    def test_or_first_true(self) -> None:
        assert evaluate("true or false") is True

    def test_or_both_false(self) -> None:
        assert evaluate("false or false") is False

    def test_not_true(self) -> None:
        assert evaluate("not true") is False

    def test_not_false(self) -> None:
        assert evaluate("not false") is True

    def test_and_returns_falsy_value(self) -> None:
        assert evaluate("null and 1") is None

    def test_or_returns_truthy_value(self) -> None:
        assert evaluate("null or 42") == 42

    def test_not_on_number(self) -> None:
        assert evaluate("not 0") is True

    def test_not_on_nonempty_string(self) -> None:
        assert evaluate('not "x"') is False


# ---------------------------------------------------------------------------
# Unary minus
# ---------------------------------------------------------------------------


class TestUnary:
    def test_negate_number(self) -> None:
        assert evaluate("-5") == -5

    def test_negate_non_number(self) -> None:
        assert evaluate('-"x"') == 0


# ---------------------------------------------------------------------------
# Built-in functions
# ---------------------------------------------------------------------------


class TestFuncLen:
    def test_string(self) -> None:
        assert evaluate('len("hello")') == 5

    def test_list(self) -> None:
        assert evaluate("len({{ x }})", {"x": [1, 2, 3]}) == 3

    def test_dict(self) -> None:
        assert evaluate("len({{ x }})", {"x": {"a": 1}}) == 1

    def test_non_collection(self) -> None:
        assert evaluate("len({{ x }})", {"x": 42}) == 0

    def test_wrong_arity(self) -> None:
        with pytest.raises(SELSyntaxError, match="len.*1 argument"):
            evaluate('len("a", "b")')


class TestFuncStringOps:
    def test_lower(self) -> None:
        assert evaluate('lower("FOO")') == "foo"

    def test_upper(self) -> None:
        assert evaluate('upper("bar")') == "BAR"

    def test_lower_no_args(self) -> None:
        assert evaluate("lower()") == ""

    def test_contains_string(self) -> None:
        assert evaluate('contains("hello world", "world")') is True

    def test_contains_string_miss(self) -> None:
        assert evaluate('contains("hello", "xyz")') is False

    def test_contains_list(self) -> None:
        assert evaluate("contains({{ x }}, 2)", {"x": [1, 2, 3]}) is True

    def test_contains_non_collection(self) -> None:
        assert evaluate("contains({{ x }}, 1)", {"x": 42}) is False

    def test_contains_wrong_arity(self) -> None:
        with pytest.raises(SELSyntaxError):
            evaluate('contains("a")')

    def test_startswith(self) -> None:
        assert evaluate('startswith("hello", "hel")') is True

    def test_startswith_miss(self) -> None:
        assert evaluate('startswith("hello", "xyz")') is False

    def test_endswith(self) -> None:
        assert evaluate('endswith("hello", "llo")') is True


class TestFuncTypeConversion:
    def test_int_from_string(self) -> None:
        assert evaluate('int("42")') == 42

    def test_int_from_none(self) -> None:
        assert evaluate("int(null)") == 0

    def test_int_from_bool_true(self) -> None:
        assert evaluate("int(true)") == 1

    def test_int_from_bool_false(self) -> None:
        assert evaluate("int(false)") == 0

    def test_int_unconvertible(self) -> None:
        with pytest.raises(SELSyntaxError, match="int.*cannot convert"):
            evaluate('int("abc")')

    def test_float_from_string(self) -> None:
        assert evaluate('float("3.14")') == pytest.approx(3.14)

    def test_float_from_none(self) -> None:
        assert evaluate("float(null)") == pytest.approx(0.0)

    def test_float_unconvertible(self) -> None:
        with pytest.raises(SELSyntaxError, match="float.*cannot convert"):
            evaluate('float("abc")')

    def test_str_from_number(self) -> None:
        assert evaluate("str(42)") == "42"

    def test_str_from_none(self) -> None:
        assert evaluate("str(null)") == "null"

    def test_str_from_bool(self) -> None:
        assert evaluate("str(true)") == "true"
        assert evaluate("str(false)") == "false"


class TestFuncJsonGet:
    def test_dotted_path(self) -> None:
        data = {"response": {"status": 200}}
        assert evaluate('json_get({{ x }}, "response.status")', {"x": data}) == 200

    def test_bracket_index(self) -> None:
        data = {"items": [10, 20, 30]}
        assert evaluate('json_get({{ x }}, "items[1]")', {"x": data}) == 20

    def test_bracket_string_key(self) -> None:
        data = {"a": {"b-c": 42}}
        assert evaluate("json_get({{ x }}, \"a['b-c']\")", {"x": data}) == 42

    def test_missing_path(self) -> None:
        assert evaluate('json_get({{ x }}, "a.b")', {"x": {"a": {}}}) is None


class TestFuncCoalesce:
    def test_first_non_null(self) -> None:
        assert evaluate("coalesce(null, null, 42)") == 42

    def test_all_null(self) -> None:
        assert evaluate("coalesce(null, null)") is None

    def test_first_value(self) -> None:
        assert evaluate("coalesce(1, 2)") == 1


class TestFuncMath:
    def test_abs_positive(self) -> None:
        assert evaluate("abs(5)") == 5

    def test_abs_negative(self) -> None:
        assert evaluate("abs(-5)") == 5

    def test_abs_non_number(self) -> None:
        assert evaluate("abs({{ x }})", {"x": "text"}) == 0

    def test_min(self) -> None:
        assert evaluate("min(3, 1, 2)") == 1

    def test_max(self) -> None:
        assert evaluate("max(3, 1, 2)") == 3

    def test_min_no_args(self) -> None:
        with pytest.raises(SELSyntaxError, match="min.*at least 1"):
            evaluate("min()")

    def test_max_no_args(self) -> None:
        with pytest.raises(SELSyntaxError, match="max.*at least 1"):
            evaluate("max()")

    def test_min_no_numbers(self) -> None:
        assert evaluate('min("a", "b")') is None


class TestFuncNowUnix:
    def test_returns_float(self) -> None:
        result = evaluate("now_unix()")
        assert isinstance(result, float)
        assert result > 0


# ---------------------------------------------------------------------------
# Budget + security enforcement
# ---------------------------------------------------------------------------


class TestBudgetAndSecurity:
    def test_expression_too_long(self) -> None:
        with pytest.raises(SELBudgetExceeded):
            evaluate("x" * 1001)

    def test_validate_too_long(self) -> None:
        with pytest.raises(SELBudgetExceeded):
            validate("x" * 1001)

    def test_forbidden_function(self) -> None:
        with pytest.raises(SELForbiddenConstruct):
            evaluate("exec(1)")

    def test_validate_rejects_forbidden(self) -> None:
        with pytest.raises(SELForbiddenConstruct):
            validate("exec(1)")


# ---------------------------------------------------------------------------
# Compound expressions
# ---------------------------------------------------------------------------


class TestCompoundExpressions:
    def test_function_on_result(self) -> None:
        assert evaluate('lower("ABC") == "abc"') is True

    def test_comparison_with_function(self) -> None:
        assert evaluate("len({{ x }}) > 2", {"x": [1, 2, 3]}) is True

    def test_boolean_with_comparison(self) -> None:
        assert evaluate("{{ x }} > 0 and {{ x }} < 10", {"x": 5}) is True

    def test_arithmetic_in_comparison(self) -> None:
        assert evaluate("{{ x }} + 1 == 6", {"x": 5}) is True

    def test_coalesce_with_default(self) -> None:
        assert evaluate('coalesce({{ x }}, "default")', {}) == "default"


# ---------------------------------------------------------------------------
# Template interpolation
# ---------------------------------------------------------------------------


class TestTemplateInterpolate:
    def test_simple_var(self) -> None:
        assert interpolate("Hello {{ name }}", {"name": "World"}) == "Hello World"

    def test_dotted_var(self) -> None:
        assert interpolate("{{ a.b }}", {"a": {"b": 42}}) == "42"

    def test_unresolved_kept_literal(self) -> None:
        assert interpolate("{{ missing }}", {}) == "{{ missing }}"

    def test_trigger_scope(self) -> None:
        result = interpolate("{{ trigger.event }}", {"__trigger__": {"event": "click"}})
        assert result == "click"

    def test_ctx_scope(self) -> None:
        result = interpolate("{{ ctx.id }}", {"__ctx__": {"id": "abc"}})
        assert result == "abc"

    def test_list_index(self) -> None:
        assert interpolate("{{ items[0] }}", {"items": ["first"]}) == "first"

    def test_bracket_string_key(self) -> None:
        assert interpolate('{{ x["key"] }}', {"x": {"key": "val"}}) == "val"

    def test_multiple_placeholders(self) -> None:
        result = interpolate("{{ a }} and {{ b }}", {"a": "X", "b": "Y"})
        assert result == "X and Y"

    def test_no_placeholders(self) -> None:
        assert interpolate("plain text", {}) == "plain text"

    def test_none_formats_as_null(self) -> None:
        assert interpolate("{{ x }}", {"x": None}) == "null"

    def test_bool_formats(self) -> None:
        assert interpolate("{{ x }}", {"x": True}) == "true"
        assert interpolate("{{ x }}", {"x": False}) == "false"

    def test_nested_list_in_dict(self) -> None:
        result = interpolate("{{ a.b[1] }}", {"a": {"b": [10, 20]}})
        assert result == "20"

    def test_out_of_bounds_kept_literal(self) -> None:
        assert interpolate("{{ items[99] }}", {"items": [1]}) == "{{ items[99] }}"

    def test_none_variables_treated_as_empty(self) -> None:
        assert interpolate("{{ x }}", None) == "{{ x }}"
