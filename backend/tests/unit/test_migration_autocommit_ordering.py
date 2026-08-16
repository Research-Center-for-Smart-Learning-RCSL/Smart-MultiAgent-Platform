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


def _is_autocommit_with(stmt: ast.stmt) -> bool:
    """True when ``stmt`` is itself a ``with ... autocommit_block():``."""
    if not isinstance(stmt, ast.With):
        return False
    return any(
        isinstance(item.context_expr, ast.Call)
        and isinstance(item.context_expr.func, ast.Attribute)
        and item.context_expr.func.attr == "autocommit_block"
        for item in stmt.items
    )


def _encloses_autocommit_block(stmt: ast.stmt) -> bool:
    """True when an autocommit block is ``stmt`` or lies anywhere inside it.

    The whole subtree, not just the top level: a block nested in an ``if``/``for``/
    ``try`` commits everything issued before it exactly as a top-level one does, so
    hiding it inside a conditional must not buy an exemption from the rule.
    """
    return any(_is_autocommit_with(node) for node in ast.walk(stmt))


def _autocommit_block_indices(body: list[ast.stmt]) -> list[int]:
    """Indices of every statement that is, or encloses, an autocommit block."""
    return [i for i, stmt in enumerate(body) if _encloses_autocommit_block(stmt)]


def _issues_a_statement_before_a_block(fn: ast.FunctionDef) -> bool:
    """True when ``fn`` runs anything that is not itself a block before its last one.

    Measured against the LAST block, not the first. A body shaped
    block / add_column / block reads clean by the first block's index and is still
    broken: the second block commits the ``add_column`` while the stamp is behind.

    A docstring does not count -- it is an expression, not DDL.
    """
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    indices = _autocommit_block_indices(body)
    if not indices:
        return False
    blocks = set(indices)
    return any(i not in blocks for i in range(max(indices)))


def _migration_files() -> list[Path]:
    return sorted(p for p in _VERSIONS.glob("*.py") if p.name != "__init__.py")


def _offending_functions(path: Path) -> list[str]:
    """Names of ``upgrade``/``downgrade`` in ``path`` with a statement before a block."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in _MIGRATION_BODIES
        and _issues_a_statement_before_a_block(node)
    ]


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


def _offends(source: str) -> bool:
    """Run the real detector over a literal migration body."""
    fn = ast.parse(source).body[0]
    assert isinstance(fn, ast.FunctionDef)
    return _issues_a_statement_before_a_block(fn)


def test_the_check_detects_a_violation() -> None:
    """The parametrized test above passes trivially if the detector is broken."""
    assert _offends(
        "def upgrade():\n"
        "    op.add_column('t', c)\n"
        "    with op.get_context().autocommit_block():\n"
        "        op.execute('CREATE INDEX CONCURRENTLY i ON t (c)')\n"
    )
    assert not _offends(
        "def upgrade():\n"
        "    with op.get_context().autocommit_block():\n"
        "        op.execute('CREATE INDEX CONCURRENTLY i ON t (c)')\n"
        "    op.create_table('u')\n"
    )


def test_a_statement_between_two_blocks_is_caught() -> None:
    """The gap a first-block-only check leaves open.

    0071, 0072 and 0074 already use two blocks per file, so this shape is the
    house style rather than a hypothetical. Keyed on the first block alone the
    body reads clean -- and the second block still commits the ``add_column``
    while ``alembic_version`` names the previous revision, which is the whole
    defect [O4.04] exists to prevent.
    """
    assert _offends(
        "def upgrade():\n"
        "    with op.get_context().autocommit_block():\n"
        "        op.execute('CREATE INDEX CONCURRENTLY i ON t (c)')\n"
        "    op.add_column('t', c2)\n"
        "    with op.get_context().autocommit_block():\n"
        "        op.execute('CREATE INDEX CONCURRENTLY j ON t (c2)')\n"
    )
    # Two adjacent blocks are fine: nothing is issued between them.
    assert not _offends(
        "def upgrade():\n"
        "    with op.get_context().autocommit_block():\n"
        "        op.execute('CREATE INDEX CONCURRENTLY i ON t (c)')\n"
        "    with op.get_context().autocommit_block():\n"
        "        op.execute('CREATE INDEX CONCURRENTLY j ON t (c2)')\n"
        "    op.create_table('u')\n"
    )


def test_a_block_nested_in_a_conditional_is_still_seen() -> None:
    """Walking only the top level would let an ``if`` hide the block entirely."""
    assert _offends(
        "def upgrade():\n"
        "    op.add_column('t', c)\n"
        "    if bind.dialect.name == 'postgresql':\n"
        "        with op.get_context().autocommit_block():\n"
        "            op.execute('CREATE INDEX CONCURRENTLY i ON t (c)')\n"
    )


def test_a_comment_mentioning_the_block_does_not_trip_the_check() -> None:
    """0071 mentions ``autocommit_block`` in prose; only the statement counts."""
    assert not _offends("def upgrade():\n    # autocommit_block is used below\n    op.add_column('t', c)\n")
