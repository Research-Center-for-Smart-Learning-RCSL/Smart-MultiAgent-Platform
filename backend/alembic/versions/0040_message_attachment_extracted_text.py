"""Add durable extracted text to message_attachments.

Adds ``extracted_text`` (nullable Text), ``extraction_status`` (enum
``message_attachment_extraction_status``: pending/extracted/empty/
unsupported/failed, default ``pending``), and ``extracted_at`` (nullable
timestamptz) to ``message_attachments``. Chat-uploaded files were previously
only visible to the model on the single turn where the file's message
happened to be the room's latest — this persists a parsed text excerpt so
later turns can still ground on the file's content instead of only the
agent's own past prose.

Existing rows default to ``pending`` / ``NULL`` — identical to "not
extracted yet" from every downstream reader's point of view, so no backfill
is needed.

Revision ID: 0040_message_attachment_extracted_text
Revises: 0039_agent_effort
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0040_message_attachment_extracted_text"
down_revision: str = "0039_agent_effort"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_extraction_status = pg.ENUM(
    "pending",
    "extracted",
    "empty",
    "unsupported",
    "failed",
    name="message_attachment_extraction_status",
    create_type=False,
)


def upgrade() -> None:
    _extraction_status.create(op.get_bind(), checkfirst=True)
    op.add_column("message_attachments", sa.Column("extracted_text", sa.Text(), nullable=True))
    op.add_column(
        "message_attachments",
        sa.Column(
            "extraction_status",
            _extraction_status,
            nullable=False,
            server_default=sa.text("'pending'::message_attachment_extraction_status"),
        ),
    )
    op.add_column(
        "message_attachments", sa.Column("extracted_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("message_attachments", "extracted_at")
    op.drop_column("message_attachments", "extraction_status")
    op.drop_column("message_attachments", "extracted_text")
    _extraction_status.drop(op.get_bind(), checkfirst=True)
