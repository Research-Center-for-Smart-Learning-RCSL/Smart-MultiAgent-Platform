"""Normalise every stored ``users.display_name`` once.

Google sign-in wrote the profile ``name`` claim without the account-name rule
([R13.34]), and rows from before that rule existed were never repaired, so a stored
name can still carry a newline or a bidi override. Rows written by Google cannot be
told apart from any other, so this covers every account; it is idempotent for a
name that is already clean. A name that normalises to nothing becomes ``NULL``,
the state a cleared name has.

The rule is copied here as it stands at this revision, not imported from
``shared_kernel.labels``, so a later change to that module cannot alter what this
migration does. One input stays outside the copy: grapheme segmentation follows
the Unicode tables of the installed ``regex`` release, so a database that applies
this later under a newer release can cut a name over 50 characters at a slightly
different cluster boundary. Every result is still a valid normalised name.

Rows are read in pages ordered by id, so the whole column is never held in
memory at once. The updates still share the migration's one transaction, which
touches only rows whose name actually changes.

The downgrade does nothing. The stripped characters are not recoverable, and they
were never valid input.

Numbered 0100 as the dossier claimed it; it revises 0102, the head at build time.

Dossier: ``docs/tasks/2026-10-07-google-display-name-normalisation``.

Revision ID: 0100_normalise_account_display_names
Revises: 0102_messages_created_at_clock_timestamp
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

import regex
import sqlalchemy as sa

from alembic import op

revision: str = "0100_normalise_account_display_names"
down_revision: str = "0102_messages_created_at_clock_timestamp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAX_DISPLAY_NAME = 50
_KEEP = ("\u200d", "\ufe0f")  # ZERO WIDTH JOINER, VARIATION SELECTOR-16
_GRAPHEME = regex.compile(r"\X")


def _normalise(raw: str) -> str | None:
    cleaned = "".join(
        ch for ch in raw if ch in _KEEP or ch == " " or not unicodedata.category(ch).startswith("C")
    ).strip()
    if len(cleaned) > _MAX_DISPLAY_NAME:
        kept = 0
        for match in _GRAPHEME.finditer(cleaned):
            size = match.end() - match.start()
            if kept + size > _MAX_DISPLAY_NAME:
                break
            kept += size
        cleaned = cleaned[: kept or 1]
    return cleaned.strip() or None


_PAGE = 1000


def upgrade() -> None:
    conn = op.get_bind()
    select = sa.text(
        "SELECT id, display_name FROM users "
        "WHERE display_name IS NOT NULL AND (CAST(:after AS uuid) IS NULL OR id > CAST(:after AS uuid)) "
        "ORDER BY id LIMIT :page"
    )
    update = sa.text("UPDATE users SET display_name = :name WHERE id = :id")
    after = None
    while rows := conn.execute(select, {"after": after, "page": _PAGE}).all():
        changed = [
            {"id": row.id, "name": normalised}
            for row in rows
            if (normalised := _normalise(row.display_name)) != row.display_name
        ]
        if changed:
            conn.execute(update, changed)
        after = rows[-1].id


def downgrade() -> None:
    pass
