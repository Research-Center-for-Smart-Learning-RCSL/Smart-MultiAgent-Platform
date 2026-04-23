"""SEL v1 recursive-descent parser.

Grammar (from workflow.schema.md §3.1):

    expression  = or_expr
    or_expr     = and_expr ("or" and_expr)*
    and_expr    = not_expr ("and" not_expr)*
    not_expr    = "not" not_expr | cmp_expr
    cmp_expr    = add_expr ( ("=="|"!="|"<"|"<="|">"|">=") add_expr )?
    add_expr    = mul_expr ( ("+"|"-") mul_expr )*
    mul_expr    = unary  ( ("*"|"/"|"%") unary )*
    unary       = "-" unary | primary
    primary     = literal | var_ref | func_call | "(" expression ")"
    literal     = number | string | "true" | "false" | "null"
    var_ref     = "{{" IDENT ("." IDENT | "[" (INT | string) "]")* "}}"
    func_call   = IDENT "(" [ expression ("," expression)* ] ")"
"""

from __future__ import annotations

from contexts.workflow.domain.errors import SELBudgetExceeded, SELSyntaxError
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
from contexts.workflow.sel.tokens import Token, TokenType

MAX_AST_DEPTH = 16


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def parse(self) -> ASTNode:
        node = self._or_expr(1)
        if self._peek().type != TokenType.EOF:
            raise SELSyntaxError(
                f"Unexpected token {self._peek().value!r} at pos {self._peek().pos}",
            )
        return node

    # -- helpers --

    def _peek(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _expect(self, tt: TokenType) -> Token:
        tok = self._advance()
        if tok.type != tt:
            raise SELSyntaxError(
                f"Expected {tt.value}, got {tok.value!r} at pos {tok.pos}",
            )
        return tok

    def _check_depth(self, depth: int) -> None:
        if depth > MAX_AST_DEPTH:
            raise SELBudgetExceeded(
                f"AST depth {depth} exceeds maximum of {MAX_AST_DEPTH}",
            )

    # -- grammar rules --

    def _or_expr(self, depth: int) -> ASTNode:
        self._check_depth(depth)
        left = self._and_expr(depth + 1)
        while self._peek().type == TokenType.OR:
            self._advance()
            right = self._and_expr(depth + 1)
            left = BinOp("or", left, right, depth=depth)
        return left

    def _and_expr(self, depth: int) -> ASTNode:
        self._check_depth(depth)
        left = self._not_expr(depth + 1)
        while self._peek().type == TokenType.AND:
            self._advance()
            right = self._not_expr(depth + 1)
            left = BinOp("and", left, right, depth=depth)
        return left

    def _not_expr(self, depth: int) -> ASTNode:
        self._check_depth(depth)
        if self._peek().type == TokenType.NOT:
            self._advance()
            operand = self._not_expr(depth + 1)
            return UnaryOp("not", operand, depth=depth)
        return self._cmp_expr(depth)

    def _cmp_expr(self, depth: int) -> ASTNode:
        self._check_depth(depth)
        left = self._add_expr(depth + 1)
        cmp_ops = {
            TokenType.EQ, TokenType.NEQ,
            TokenType.LT, TokenType.LTE,
            TokenType.GT, TokenType.GTE,
        }
        if self._peek().type in cmp_ops:
            op_tok = self._advance()
            right = self._add_expr(depth + 1)
            return BinOp(str(op_tok.value), left, right, depth=depth)
        return left

    def _add_expr(self, depth: int) -> ASTNode:
        self._check_depth(depth)
        left = self._mul_expr(depth + 1)
        while self._peek().type in (TokenType.PLUS, TokenType.MINUS):
            op_tok = self._advance()
            right = self._mul_expr(depth + 1)
            left = BinOp(str(op_tok.value), left, right, depth=depth)
        return left

    def _mul_expr(self, depth: int) -> ASTNode:
        self._check_depth(depth)
        left = self._unary(depth + 1)
        while self._peek().type in (TokenType.STAR, TokenType.SLASH, TokenType.PERCENT):
            op_tok = self._advance()
            right = self._unary(depth + 1)
            left = BinOp(str(op_tok.value), left, right, depth=depth)
        return left

    def _unary(self, depth: int) -> ASTNode:
        self._check_depth(depth)
        if self._peek().type == TokenType.MINUS:
            self._advance()
            operand = self._unary(depth + 1)
            return UnaryOp("-", operand, depth=depth)
        return self._primary(depth)

    def _primary(self, depth: int) -> ASTNode:
        self._check_depth(depth)
        tok = self._peek()

        # Literals
        if tok.type == TokenType.NUMBER:
            self._advance()
            return NumberLit(tok.value, depth=depth)  # type: ignore[arg-type]
        if tok.type == TokenType.STRING:
            self._advance()
            return StringLit(str(tok.value), depth=depth)
        if tok.type in (TokenType.TRUE, TokenType.FALSE):
            self._advance()
            return BoolLit(bool(tok.value), depth=depth)
        if tok.type == TokenType.NULL:
            self._advance()
            return NullLit(depth=depth)

        # Variable reference: {{ path.segments }}
        if tok.type == TokenType.VAR_REF_START:
            return self._var_ref(depth)

        # Function call or bare identifier (treated as function with 0 args or error)
        if tok.type == TokenType.IDENT:
            # Look ahead for '(' → function call
            if (
                self._pos + 1 < len(self._tokens)
                and self._tokens[self._pos + 1].type == TokenType.LPAREN
            ):
                return self._func_call(depth)
            raise SELSyntaxError(
                f"Bare identifier {tok.value!r} is not allowed; "
                f"use {{{{ {tok.value} }}}} for variables or {tok.value}() for functions "
                f"(pos {tok.pos})",
            )

        # Grouped expression
        if tok.type == TokenType.LPAREN:
            self._advance()
            expr = self._or_expr(depth + 1)
            self._expect(TokenType.RPAREN)
            return expr

        raise SELSyntaxError(f"Unexpected token {tok.value!r} at pos {tok.pos}")

    def _var_ref(self, depth: int) -> VarRef:
        self._expect(TokenType.VAR_REF_START)
        segments: list[str | int] = []

        # First segment must be an identifier
        tok = self._expect(TokenType.IDENT)
        segments.append(str(tok.value))

        # Additional path segments: .ident or [int] or [string]
        while self._peek().type in (TokenType.DOT, TokenType.LBRACKET):
            if self._peek().type == TokenType.DOT:
                self._advance()
                seg = self._expect(TokenType.IDENT)
                segments.append(str(seg.value))
            else:
                self._advance()  # [
                idx = self._peek()
                if idx.type == TokenType.NUMBER:
                    self._advance()
                    segments.append(int(idx.value))  # type: ignore[arg-type]
                elif idx.type == TokenType.STRING:
                    self._advance()
                    segments.append(str(idx.value))
                else:
                    raise SELSyntaxError(
                        f"Expected number or string index, got {idx.value!r} at pos {idx.pos}",
                    )
                self._expect(TokenType.RBRACKET)

        self._expect(TokenType.VAR_REF_END)
        return VarRef(tuple(segments), depth=depth)

    def _func_call(self, depth: int) -> FuncCall:
        name_tok = self._advance()  # IDENT
        self._expect(TokenType.LPAREN)
        args: list[ASTNode] = []
        if self._peek().type != TokenType.RPAREN:
            args.append(self._or_expr(depth + 1))
            while self._peek().type == TokenType.COMMA:
                self._advance()
                args.append(self._or_expr(depth + 1))
        self._expect(TokenType.RPAREN)
        return FuncCall(str(name_tok.value), tuple(args), depth=depth)


def parse(tokens: list[Token]) -> ASTNode:
    return Parser(tokens).parse()
