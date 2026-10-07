"""Normalise every stored ``users.display_name`` once.

Google sign-in wrote the profile ``name`` claim without the account-name rule
([R13.34]), and rows from before that rule existed were never repaired, so a stored
name can still carry a newline or a bidi override. Rows written by Google cannot be
told apart from any other, so this covers every account; it is idempotent for a
name that is already clean. A name that normalises to nothing becomes ``NULL``,
the state a cleared name has.

The rule is copied here as it stands at this revision, not imported from
``shared_kernel.labels``: a later change to that module must not alter what an
already-applied migration did.

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
_KEEP = ("‍", "️")  # ZERO WIDTH JOINER, VARIATION SELECTOR-16
_GRAPHEME = regex.compile(r"\X")


def _normalise(raw: str) -> str | None:
    cleaned = "".join(
        ch for ch in raw if ch in _KEEP or ch == " " or not unicodedata.category(ch).startswith("C")
    ).strip()
    if len(cleaned) > _MAX_DISPLAY_NAME:
        kept = 0
        for cluster in _GRAPHEME.findall(cleaned):
            if kept + len(cluster) > _MAX_DISPLAY_NAME:
                break
            kept += len(cluster)
        cleaned = cleaned[:kept]
    return cleaned.strip() or None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, display_name FROM users WHERE display_name IS NOT NULL")).all()
    changed = [
        {"id": row.id, "name": normalised}
        for row in rows
        if (normalised := _normalise(row.display_name)) != row.display_name
    ]
    if changed:
        conn.execute(sa.text("UPDATE users SET display_name = :name WHERE id = :id"), changed)


def downgrade() -> None:
    pass
