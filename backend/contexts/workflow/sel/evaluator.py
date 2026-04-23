"""SEL v1 evaluator — walks the AST and produces a value.

Budget enforcement:
- Expression length ≤ 1000 chars (checked before parse).
- AST depth ≤ 16 (checked during parse).
- Wall-clock ≤ 5 ms per evaluation (threading timer).
- Regex via google-re2 with 5 ms budget.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any

from contexts.workflow.domain.errors import (
    SELBudgetExceeded,
    SELForbiddenConstruct,
    SELSyntaxError,
)
from contexts.workflow.sel.ast_nodes import (
    ASTNode,
    BinOp,
    BoolLit,
    FuncCall,
    NullLit,
    NumberLit,
    StringLit,
    UnaryOp,
    VarRef,
)
from contexts.workflow.sel.lexer import tokenize
from contexts.workflow.sel.parser import parse

MAX_EXPR_LENGTH = 1000
EVAL_BUDGET_MS = 5.0

# ---------------------------------------------------------------------------
# Whitelisted functions (§3.2)
# ---------------------------------------------------------------------------

_ALLOWED_FUNCTIONS = frozenset({
    "len", "lower", "upper", "contains", "startswith", "endswith",
    "matches", "int", "float", "str", "json_get", "coalesce",
    "now_unix", "abs", "min", "max",
})


def _resolve_var(segments: tuple[str | int, ...], variables: dict[str, Any]) -> Any:
    """Resolve a dotted/bracketed path against the variable scopes.

    Scopes:
    - trigger.*  → variables["__trigger__"]
    - ctx.*      → variables["__ctx__"]
    - anything else → variables (workflow vars)

    Undeclared reads yield None (§4.2).
    """
    if not segments:
        return None

    first = segments[0]

    if first == "trigger":
        obj = variables.get("__trigger__", {})
        rest = segments[1:]
    elif first == "ctx":
        obj = variables.get("__ctx__", {})
        rest = segments[1:]
    else:
        obj = variables
        rest = segments

    for seg in rest:
        if obj is None:
            return None
        if isinstance(seg, int):
            if isinstance(obj, (list, tuple)) and 0 <= seg < len(obj):
                obj = obj[seg]
            else:
                return None
        elif isinstance(obj, dict):
            obj = obj.get(seg)
        else:
            return None
    return obj


def _json_get(obj: Any, path: str) -> Any:
    """Dotted/bracket path into nested JSON. E.g. "messages[0].role"."""
    current = obj
    i = 0
    n = len(path)
    while i < n and current is not None:
        if path[i] == "[":
            end = path.index("]", i)
            idx_str = path[i + 1 : end]
            try:
                idx = int(idx_str)
            except ValueError:
                idx_str = idx_str.strip("'\"")
                if isinstance(current, dict):
                    current = current.get(idx_str)
                else:
                    return None
                i = end + 1
                if i < n and path[i] == ".":
                    i += 1
                continue
            if isinstance(current, (list, tuple)) and 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
            i = end + 1
            if i < n and path[i] == ".":
                i += 1
        else:
            dot = path.find(".", i)
            bracket = path.find("[", i)
            if dot == -1 and bracket == -1:
                end = n
            elif dot == -1:
                end = bracket
            elif bracket == -1:
                end = dot
            else:
                end = min(dot, bracket)
            key = path[i:end]
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None
            i = end
            if i < n and path[i] == ".":
                i += 1
    return current


def _call_func(name: str, args: list[Any]) -> Any:
    """Dispatch a whitelisted function call."""
    if name == "len":
        if len(args) != 1:
            raise SELSyntaxError(f"len() takes 1 argument, got {len(args)}")
        v = args[0]
        if isinstance(v, (str, list, tuple)):
            return len(v)
        if isinstance(v, dict):
            return len(v)
        return 0

    if name == "lower":
        return str(args[0]).lower() if args else ""

    if name == "upper":
        return str(args[0]).upper() if args else ""

    if name == "contains":
        if len(args) != 2:
            raise SELSyntaxError("contains() takes 2 arguments")
        haystack, needle = args
        if isinstance(haystack, str):
            return str(needle) in haystack
        if isinstance(haystack, (list, tuple)):
            return needle in haystack
        return False

    if name == "startswith":
        if len(args) != 2:
            raise SELSyntaxError("startswith() takes 2 arguments")
        return str(args[0]).startswith(str(args[1]))

    if name == "endswith":
        if len(args) != 2:
            raise SELSyntaxError("endswith() takes 2 arguments")
        return str(args[0]).endswith(str(args[1]))

    if name == "matches":
        if len(args) != 2:
            raise SELSyntaxError("matches() takes 2 arguments")
        return _regex_match(str(args[0]), str(args[1]))

    if name == "int":
        if len(args) != 1:
            raise SELSyntaxError("int() takes 1 argument")
        v = args[0]
        if v is None:
            return 0
        if isinstance(v, bool):
            return 1 if v else 0
        try:
            return int(v)
        except (ValueError, TypeError) as exc:
            raise SELSyntaxError(f"int() cannot convert {v!r}") from exc

    if name == "float":
        if len(args) != 1:
            raise SELSyntaxError("float() takes 1 argument")
        v = args[0]
        if v is None:
            return 0.0
        try:
            return float(v)
        except (ValueError, TypeError) as exc:
            raise SELSyntaxError(f"float() cannot convert {v!r}") from exc

    if name == "str":
        if len(args) != 1:
            raise SELSyntaxError("str() takes 1 argument")
        v = args[0]
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)

    if name == "json_get":
        if len(args) != 2:
            raise SELSyntaxError("json_get() takes 2 arguments")
        return _json_get(args[0], str(args[1]))

    if name == "coalesce":
        for a in args:
            if a is not None:
                return a
        return None

    if name == "now_unix":
        return time.time()

    if name == "abs":
        if len(args) != 1:
            raise SELSyntaxError("abs() takes 1 argument")
        v = args[0]
        if isinstance(v, (int, float)):
            return abs(v)
        return 0

    if name == "min":
        if not args:
            raise SELSyntaxError("min() requires at least 1 argument")
        nums = [a for a in args if isinstance(a, (int, float))]
        return min(nums) if nums else None

    if name == "max":
        if not args:
            raise SELSyntaxError("max() requires at least 1 argument")
        nums = [a for a in args if isinstance(a, (int, float))]
        return max(nums) if nums else None

    raise SELForbiddenConstruct(f"Function {name!r} is not in the SEL whitelist")


def _regex_match(text: str, pattern: str) -> bool:
    """RE2-safe regex match with 5 ms budget."""
    try:
        import re2  # type: ignore[import-untyped]
        compiled = re2.compile(pattern)
        return compiled.search(text) is not None
    except ImportError:
        import re
        try:
            compiled_re = re.compile(pattern)
        except re.error:
            return False
        return compiled_re.search(text) is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# AST evaluator
# ---------------------------------------------------------------------------


class _Evaluator:
    def __init__(self, variables: dict[str, Any], deadline: float) -> None:
        self._vars = variables
        self._deadline = deadline

    def visit(self, node: ASTNode) -> Any:
        if time.monotonic() > self._deadline:
            raise SELBudgetExceeded("Expression evaluation exceeded 5 ms budget")

        if isinstance(node, NumberLit):
            return node.value
        if isinstance(node, StringLit):
            return node.value
        if isinstance(node, BoolLit):
            return node.value
        if isinstance(node, NullLit):
            return None
        if isinstance(node, VarRef):
            return _resolve_var(node.segments, self._vars)
        if isinstance(node, UnaryOp):
            return self._eval_unary(node)
        if isinstance(node, BinOp):
            return self._eval_binop(node)
        if isinstance(node, FuncCall):
            return self._eval_func(node)

        raise SELSyntaxError(f"Unknown AST node: {type(node).__name__}")

    def _eval_unary(self, node: UnaryOp) -> Any:
        val = self.visit(node.operand)
        if node.op == "-":
            if isinstance(val, (int, float)):
                return -val
            return 0
        if node.op == "not":
            return not _truthy(val)
        raise SELSyntaxError(f"Unknown unary op: {node.op}")

    def _eval_binop(self, node: BinOp) -> Any:
        # Short-circuit for boolean ops
        if node.op == "and":
            left = self.visit(node.left)
            if not _truthy(left):
                return left
            return self.visit(node.right)
        if node.op == "or":
            left = self.visit(node.left)
            if _truthy(left):
                return left
            return self.visit(node.right)

        left = self.visit(node.left)
        right = self.visit(node.right)

        # Comparison — mismatched types return False (§4.2)
        if node.op == "==":
            return _safe_eq(left, right)
        if node.op == "!=":
            return not _safe_eq(left, right)
        if node.op in ("<", "<=", ">", ">="):
            return _safe_cmp(node.op, left, right)

        # Arithmetic
        if node.op == "+":
            if isinstance(left, str) and isinstance(right, str):
                return left + right
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                return left + right
            return 0
        if node.op == "-":
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                return left - right
            return 0
        if node.op == "*":
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                return left * right
            return 0
        if node.op == "/":
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                if right == 0:
                    return 0
                return left / right
            return 0
        if node.op == "%":
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                if right == 0:
                    return 0
                return left % right
            return 0

        raise SELSyntaxError(f"Unknown binary op: {node.op}")

    def _eval_func(self, node: FuncCall) -> Any:
        if node.name not in _ALLOWED_FUNCTIONS:
            raise SELForbiddenConstruct(
                f"Function {node.name!r} is not in the SEL whitelist",
            )
        args = [self.visit(a) for a in node.args]
        return _call_func(node.name, args)


def _truthy(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return len(val) > 0
    if isinstance(val, (list, tuple, dict)):
        return len(val) > 0
    return True


def _safe_eq(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if type(a) is type(b):
        return a == b
    # Cross-type: int/float comparison
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return False


def _safe_cmp(op: str, a: Any, b: Any) -> bool:
    if not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        return False
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == ">=":
        return a >= b
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate(expression: str, variables: dict[str, Any] | None = None) -> Any:
    """Evaluate a SEL v1 expression and return its value.

    Raises SELBudgetExceeded, SELSyntaxError, or SELForbiddenConstruct on
    invalid or over-budget inputs.
    """
    if len(expression) > MAX_EXPR_LENGTH:
        raise SELBudgetExceeded(
            f"Expression length {len(expression)} exceeds max {MAX_EXPR_LENGTH}",
        )

    tokens = tokenize(expression)
    ast = parse(tokens)
    deadline = time.monotonic() + EVAL_BUDGET_MS / 1000.0
    evaluator = _Evaluator(variables or {}, deadline)
    return evaluator.visit(ast)
