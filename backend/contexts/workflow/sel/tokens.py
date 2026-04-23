"""SEL v1 token definitions."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class TokenType(enum.Enum):
    # Literals
    NUMBER = "NUMBER"
    STRING = "STRING"
    TRUE = "TRUE"
    FALSE = "FALSE"
    NULL = "NULL"

    # Identifiers & variable refs
    IDENT = "IDENT"
    VAR_REF_START = "{{"
    VAR_REF_END = "}}"

    # Operators
    PLUS = "+"
    MINUS = "-"
    STAR = "*"
    SLASH = "/"
    PERCENT = "%"
    EQ = "=="
    NEQ = "!="
    LT = "<"
    LTE = "<="
    GT = ">"
    GTE = ">="

    # Keywords
    AND = "and"
    OR = "or"
    NOT = "not"

    # Delimiters
    LPAREN = "("
    RPAREN = ")"
    LBRACKET = "["
    RBRACKET = "]"
    DOT = "."
    COMMA = ","

    # End
    EOF = "EOF"


@dataclass(frozen=True, slots=True)
class Token:
    type: TokenType
    value: object
    pos: int
