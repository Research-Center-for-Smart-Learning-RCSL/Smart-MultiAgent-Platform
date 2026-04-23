"""messages + message_edits + message_attachments — F.3 / §21.1.

`content_tsv` is maintained synchronously by a trigger on insert/update of
`content_md` so full-text search (F.10) stays in sync without an async job.
Partial indexes respect the `deleted_at IS NULL` soft-delete semantics used
elsewhere in the schema (messages are hard-deleted on manual delete per
R13.16 so the filter is belt-and-braces for retention edge-cases).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0017_messages"
down_revision: str | Sequence[str] | None = "0016_chatrooms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SENDER_TYPES: tuple[str, ...] = ("user", "agent", "system")
_ATTACHMENT_STATUS: tuple[str, ...] = ("active", "quarantined", "expired")
_SCAN_STATUS: tuple[str, ...] = ("pending", "clean", "quarantined", "skipped")


def upgrade() -> None:
    op.execute(
        "CREATE TYPE message_sender_type AS ENUM ("
        + ", ".join(f"'{v}'" for v in _SENDER_TYPES) + ")"
    )
    op.execute(
        "CREATE TYPE message_attachment_status AS ENUM ("
        + ", ".join(f"'{v}'" for v in _ATTACHMENT_STATUS) + ")"
    )
    op.execute(
        "CREATE TYPE message_scan_status AS ENUM ("
        + ", ".join(f"'{v}'" for v in _SCAN_STATUS) + ")"
    )

    op.create_table(
        "messages",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("chatroom_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("sender_type",
                  pg.ENUM(*_SENDER_TYPES, name="message_sender_type",
                          create_type=False),
                  nullable=False),
        sa.Column("sender_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("content_md", sa.Text(), nullable=False,
                  server_default=sa.text("''")),
        sa.Column("content_tsv", pg.TSVECTOR(), nullable=True),
        sa.Column("metadata", pg.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Integer(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("edited_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # Primary listing index — R13.20 reconnect delta + normal paging both use
    # (chatroom_id, created_at DESC) on live rows only. Raw SQL so the DESC
    # sort and partial predicate are unambiguous.
    op.execute(
        "CREATE INDEX ix_messages_chatroom_created "
        "ON messages (chatroom_id, created_at DESC) "
        "WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_messages_content_tsv "
        "ON messages USING GIN (content_tsv)"
    )

    # content_tsv is maintained by a plain BEFORE-trigger so inserts/updates
    # from app code never need to touch it. English config is the platform
    # default; localisation overrides land in F.10 if needed.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION smap_message_tsv() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          NEW.content_tsv := to_tsvector('english', COALESCE(NEW.content_md, ''));
          RETURN NEW;
        END
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_messages_tsv "
        "BEFORE INSERT OR UPDATE OF content_md ON messages "
        "FOR EACH ROW EXECUTE FUNCTION smap_message_tsv();"
    )
    # version is bumped by the generic trigger from 0002_tenancy.
    op.execute(
        "CREATE TRIGGER trg_messages_bump_version "
        "BEFORE UPDATE ON messages FOR EACH ROW "
        "EXECUTE FUNCTION smap_bump_version();"
    )

    op.create_table(
        "message_edits",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("message_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("messages.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("old_content_md", sa.Text(), nullable=False),
        sa.Column("edited_by_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("edited_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_message_edits_message", "message_edits", ["message_id"],
    )

    op.create_table(
        "message_attachments",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("message_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("messages.id", ondelete="CASCADE"),
                  nullable=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("minio_path", sa.Text(), nullable=False),
        sa.Column("status",
                  pg.ENUM(*_ATTACHMENT_STATUS,
                          name="message_attachment_status",
                          create_type=False),
                  nullable=False,
                  server_default=sa.text("'active'::message_attachment_status")),
        sa.Column("scan_status",
                  pg.ENUM(*_SCAN_STATUS, name="message_scan_status",
                          create_type=False),
                  nullable=False,
                  server_default=sa.text("'pending'::message_scan_status")),
        sa.Column("scan_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "size_bytes >= 0", name="message_attachments_size_nonneg",
        ),
    )
    op.create_index(
        "ix_message_attachments_message",
        "message_attachments", ["message_id"],
    )
    op.create_index(
        "ix_message_attachments_expires",
        "message_attachments", ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_message_attachments_expires", table_name="message_attachments",
    )
    op.drop_index(
        "ix_message_attachments_message", table_name="message_attachments",
    )
    op.drop_table("message_attachments")
    op.drop_index("ix_message_edits_message", table_name="message_edits")
    op.drop_table("message_edits")
    op.execute("DROP TRIGGER IF EXISTS trg_messages_bump_version ON messages;")
    op.execute("DROP TRIGGER IF EXISTS trg_messages_tsv ON messages;")
    op.execute("DROP FUNCTION IF EXISTS smap_message_tsv();")
    op.execute("DROP INDEX IF EXISTS ix_messages_content_tsv")
    op.execute("DROP INDEX IF EXISTS ix_messages_chatroom_created")
    op.drop_table("messages")
    op.execute("DROP TYPE IF EXISTS message_scan_status")
    op.execute("DROP TYPE IF EXISTS message_attachment_status")
    op.execute("DROP TYPE IF EXISTS message_sender_type")
