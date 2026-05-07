"""SEL v1 AST node types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class NumberLit:
    value: int | float
    depth: int = 1


@dataclass(frozen=True, slots=True)
class StringLit:
    value: str
    depth: int = 1


@dataclass(frozen=True, slots=True)
class BoolLit:
    value: bool
    depth: int = 1


@dataclass(frozen=True, slots=True)
class NullLit:
    depth: int = 1


@dataclass(frozen=True, slots=True)
class VarRef:
    """Variable reference: path segments like ["answer"] or ["trigger", "message", "content"]."""

    segments: tuple[str | int, ...]
    depth: int = 1


@dataclass(frozen=True, slots=True)
class BinOp:
    op: str
    left: Any  # AST node
    right: Any  # AST node
    depth: int = 1


@dataclass(frozen=True, slots=True)
class UnaryOp:
    op: str
    operand: Any  # AST node
    depth: int = 1


@dataclass(frozen=True, slots=True)
class FuncCall:
    name: str
    args: tuple[Any, ...]
    depth: int = 1


# Union of all AST node types
ASTNode = NumberLit | StringLit | BoolLit | NullLit | VarRef | BinOp | UnaryOp | FuncCall
