"""SEL v1 — SMAP Expression Language interpreter.

Public surface:
- evaluate(expr, variables) → value
- interpolate(template, variables) ��� str
"""

from contexts.workflow.sel.evaluator import evaluate
from contexts.workflow.sel.template import interpolate

__all__ = ["evaluate", "interpolate"]
