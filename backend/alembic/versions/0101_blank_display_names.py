"""Clear stored names that have no visible character.

Until this revision the label rule stripped only control and format characters,
so a name made of Hangul fillers, the blank Braille cell, combining marks or a
lone joiner was stored and rendered as an author with no name. Such account names
and room guest labels become ``NULL``, the state of an unnamed account or guest;
guest session names and the name snapshot on a guest ban are ``NOT NULL`` and
become "Guest", the generic label an unresolved guest already reads as.

Only names with no visible character are touched. A name that merely carries an
invisible character among visible ones keeps it until it is next written, when
the current rule strips it.

The predicate is copied here as it stands at this revision, not imported from
``shared_kernel.labels``, so a later change to that module cannot alter what this
migration does. Which code points are default-ignorable or combining follows the
Unicode tables of the installed ``regex`` release and Python's ``unicodedata``.

The downgrade does nothing: the cleared names were never usable.

Dossier: ``docs/tasks/2026-10-07-display-name-validation``.

Revision ID: 0101_blank_display_names
Revises: 0100_normalise_account_display_names
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from typing import Any

import regex
import sqlalchemy as sa

from alembic import op

revision: str = "0101_blank_display_names"
down_revision: str = "0100_normalise_account_display_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEEP = ("\u200d", "\ufe0f")  # ZERO WIDTH JOINER, VARIATION SELECTOR-16
_IGNORABLE = regex.compile(r"\p{Default_Ignorable_Code_Point}")
_BLANK_GLYPH = "\u2800"  # BRAILLE PATTERN BLANK

_GUEST = "Guest"
_PAGE = 1000


def _is_blank(name: str) -> bool:
    for ch in name:
        if ch in _KEEP or ch == _BLANK_GLYPH or ch.isspace():
            continue
        category = unicodedata.category(ch)
        if category.startswith(("C", "M")) or _IGNORABLE.match(ch):
            continue
        return False
    return True


def _repair(select: sa.TextClause, update: sa.TextClause, key: tuple[str, ...], value: str | None) -> None:
    conn = op.get_bind()
    after: dict[str, Any] = dict.fromkeys(key)
    while rows := conn.execute(select, {**after, "page": _PAGE}).all():
        blank = [
            {**{k: getattr(row, k) for k in key}, "name": value}
            for row in rows
            if _is_blank(row.display_name)
        ]
        if blank:
            conn.execute(update, blank)
        after = {k: getattr(rows[-1], k) for k in key}


def upgrade() -> None:
    _repair(
        sa.text(
            "SELECT id, display_name FROM users "
            "WHERE display_name IS NOT NULL AND (CAST(:id AS uuid) IS NULL OR id > CAST(:id AS uuid)) "
            "ORDER BY id LIMIT :page"
        ),
        sa.text("UPDATE users SET display_name = :name WHERE id = :id"),
        ("id",),
        None,
    )
    _repair(
        sa.text(
            "SELECT chatroom_id, user_id, display_name FROM chatroom_guests "
            "WHERE display_name IS NOT NULL AND (CAST(:chatroom_id AS uuid) IS NULL "
            "OR (chatroom_id, user_id) > (CAST(:chatroom_id AS uuid), CAST(:user_id AS uuid))) "
            "ORDER BY chatroom_id, user_id LIMIT :page"
        ),
        sa.text(
            "UPDATE chatroom_guests SET display_name = :name "
            "WHERE chatroom_id = :chatroom_id AND user_id = :user_id"
        ),
        ("chatroom_id", "user_id"),
        None,
    )
    _repair(
        sa.text(
            "SELECT id, display_name FROM guest_sessions "
            "WHERE CAST(:id AS uuid) IS NULL OR id > CAST(:id AS uuid) "
            "ORDER BY id LIMIT :page"
        ),
        sa.text("UPDATE guest_sessions SET display_name = :name WHERE id = :id"),
        ("id",),
        _GUEST,
    )
    _repair(
        sa.text(
            "SELECT id, display_name FROM chatroom_guest_bans "
            "WHERE CAST(:id AS uuid) IS NULL OR id > CAST(:id AS uuid) "
            "ORDER BY id LIMIT :page"
        ),
        sa.text("UPDATE chatroom_guest_bans SET display_name = :name WHERE id = :id"),
        ("id",),
        _GUEST,
    )


def downgrade() -> None:
    pass
