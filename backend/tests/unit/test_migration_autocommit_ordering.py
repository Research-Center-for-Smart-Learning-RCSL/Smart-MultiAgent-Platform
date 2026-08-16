"""No migration may place a statement before an ``autocommit_block``.

The rule this pins ([O4.04]) is not obvious and is not enforced by anything else,
so it is worth stating why it exists rather than only that it does.

``op.get_context().autocommit_block()`` **unconditionally commits the transaction
that precedes it** (alembic ``runtime/migration.py``, ``_in_connection_transaction``
arm), while the revision stamp for the migration is written only *after* the
migration body returns (``head_maintainer.update_to_step`` follows
``step.migration_fn``). Those two facts together mean any statement issued before
the block can be durably committed while ``alembic_version`` still names the
previous revision. A retry then re-enters the body from the top and dies on the
already-applied statement -- ``DuplicateColumn``, ``DuplicateObject``,
``DuplicateTable`` -- and the operator has to hand-drop objects before the deploy
can move.

That is exactly what 0076 shipped with, in both directions. The instance is fixed
in that file; this test is here for the class, because the mistake is invisible in
review (the block reads as a local concern) and impossible to reproduce without a
mid-migration failure against a real PostgreSQL.

Deliberately AST-based rather than textual: a comment mentioning ``autocommit_block``
must not trip it, and 0071 has three such mentions.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
_MIGRATION_BODIES = ("upgrade", "downgrade")


def _autocommit_block_index(body: list[ast.stmt]) -> int | None:
    """Index of the first ``with ... autocommit_block():`` statement, if any."""
    for i, stmt in enumerate(body):
        if not isinstance(stmt, ast.With):
            continue
        for item in stmt.items:
            call = item.context_expr
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "autocommit_block"
            ):
                return i
    return None


def _migration_files() -> list[Path]:
    return sorted(p for p in _VERSIONS.glob("*.py") if p.name != "__init__.py")


def _offending_functions(path: Path) -> list[str]:
    """Names of ``upgrade``/``downgrade`` in ``path`` with a statement before a block.

    A docstring does not count: it is an expression, not DDL.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name not in _MIGRATION_BODIES:
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]  # drop the docstring
        index = _autocommit_block_index(body)
        if index is not None and index > 0:
            offenders.append(node.name)
    return offenders


def test_the_versions_directory_was_actually_found() -> None:
    """Guard the guard: a wrong path would make every assertion below vacuous."""
    files = _migration_files()
    assert _VERSIONS.is_dir()
    assert len(files) > 50


@pytest.mark.parametrize("path", _migration_files(), ids=lambda p: p.stem)
def test_no_statement_precedes_an_autocommit_block(path: Path) -> None:
    offenders = _offending_functions(path)
    assert offenders == [], (
        f"{path.name}: {', '.join(offenders)} issue DDL before entering an autocommit block. "
        "The block commits that DDL while the revision stamp is still the previous one, so a "
        "later failure leaves the schema advanced, the version behind, and the migration "
        "unretryable. Move every statement after the block, or drop the block."
    )


def test_the_check_detects_a_violation() -> None:
    """The parametrized test above passes trivially if the detector is broken."""
    offending = ast.parse(
        "def upgrade():\n"
        "    op.add_column('t', c)\n"
        "    with op.get_context().autocommit_block():\n"
        "        op.execute('CREATE INDEX CONCURRENTLY i ON t (c)')\n"
    )
    fn = offending.body[0]
    assert isinstance(fn, ast.FunctionDef)
    assert _autocommit_block_index(fn.body) == 1

    clean = ast.parse(
        "def upgrade():\n"
        "    with op.get_context().autocommit_block():\n"
        "        op.execute('CREATE INDEX CONCURRENTLY i ON t (c)')\n"
        "    op.create_table('u')\n"
    )
    fn = clean.body[0]
    assert isinstance(fn, ast.FunctionDef)
    assert _autocommit_block_index(fn.body) == 0


def test_a_comment_mentioning_the_block_does_not_trip_the_check() -> None:
    """0071 mentions ``autocommit_block`` in prose; only the statement counts."""
    tree = ast.parse("def upgrade():\n    # autocommit_block is used below\n    op.add_column('t', c)\n")
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)
    assert _autocommit_block_index(fn.body) is None
