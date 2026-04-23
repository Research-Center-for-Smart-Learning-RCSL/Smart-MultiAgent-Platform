"""SEL v1 lexer — tokenises expression strings."""

from __future__ import annotations

from contexts.workflow.domain.errors import SELSyntaxError
from contexts.workflow.sel.tokens import Token, TokenType

_KEYWORDS: dict[str, TokenType] = {
    "and": TokenType.AND,
    "or": TokenType.OR,
    "not": TokenType.NOT,
    "true": TokenType.TRUE,
    "false": TokenType.FALSE,
    "null": TokenType.NULL,
}

_SINGLE_CHAR: dict[str, TokenType] = {
    "+": TokenType.PLUS,
    "-": TokenType.MINUS,
    "*": TokenType.STAR,
    "/": TokenType.SLASH,
    "%": TokenType.PERCENT,
    "(": TokenType.LPAREN,
    ")": TokenType.RPAREN,
    "[": TokenType.LBRACKET,
    "]": TokenType.RBRACKET,
    ".": TokenType.DOT,
    ",": TokenType.COMMA,
}


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(source)

    while i < n:
        ch = source[i]

        # Whitespace
        if ch in " \t\r\n":
            i += 1
            continue

        # Variable reference start
        if ch == "{" and i + 1 < n and source[i + 1] == "{":
            tokens.append(Token(TokenType.VAR_REF_START, "{{", i))
            i += 2
            continue

        # Variable reference end
        if ch == "}" and i + 1 < n and source[i + 1] == "}":
            tokens.append(Token(TokenType.VAR_REF_END, "}}", i))
            i += 2
            continue

        # Two-char operators
        if i + 1 < n:
            two = source[i : i + 2]
            if two == "==":
                tokens.append(Token(TokenType.EQ, "==", i))
                i += 2
                continue
            if two == "!=":
                tokens.append(Token(TokenType.NEQ, "!=", i))
                i += 2
                continue
            if two == "<=":
                tokens.append(Token(TokenType.LTE, "<=", i))
                i += 2
                continue
            if two == ">=":
                tokens.append(Token(TokenType.GTE, ">=", i))
                i += 2
                continue

        # Single-char operators
        if ch == "<":
            tokens.append(Token(TokenType.LT, "<", i))
            i += 1
            continue
        if ch == ">":
            tokens.append(Token(TokenType.GT, ">", i))
            i += 1
            continue

        if ch in _SINGLE_CHAR:
            tokens.append(Token(_SINGLE_CHAR[ch], ch, i))
            i += 1
            continue

        # Number
        if ch.isdigit():
            start = i
            while i < n and (source[i].isdigit() or source[i] == "."):
                i += 1
            text = source[start:i]
            val: int | float = float(text) if "." in text else int(text)
            tokens.append(Token(TokenType.NUMBER, val, start))
            continue

        # String (single or double quotes)
        if ch in ('"', "'"):
            quote = ch
            start = i
            i += 1
            parts: list[str] = []
            while i < n and source[i] != quote:
                if source[i] == "\\" and i + 1 < n:
                    esc = source[i + 1]
                    if esc == "n":
                        parts.append("\n")
                    elif esc == "t":
                        parts.append("\t")
                    elif esc == "\\":
                        parts.append("\\")
                    elif esc == quote:
                        parts.append(quote)
                    else:
                        parts.append(esc)
                    i += 2
                else:
                    parts.append(source[i])
                    i += 1
            if i >= n:
                raise SELSyntaxError(f"Unterminated string at pos {start}")
            i += 1  # closing quote
            tokens.append(Token(TokenType.STRING, "".join(parts), start))
            continue

        # Identifiers and keywords
        if ch.isalpha() or ch == "_":
            start = i
            while i < n and (source[i].isalnum() or source[i] == "_"):
                i += 1
            text = source[start:i]
            tt = _KEYWORDS.get(text, TokenType.IDENT)
            val = text if tt == TokenType.IDENT else text
            if tt == TokenType.TRUE:
                tokens.append(Token(tt, True, start))
            elif tt == TokenType.FALSE:
                tokens.append(Token(tt, False, start))
            elif tt == TokenType.NULL:
                tokens.append(Token(tt, None, start))
            else:
                tokens.append(Token(tt, text, start))
            continue

        raise SELSyntaxError(f"Unexpected character {ch!r} at pos {i}")

    tokens.append(Token(TokenType.EOF, None, i))
    return tokens
