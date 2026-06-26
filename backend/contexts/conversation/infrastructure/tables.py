"""SQLAlchemy Core tables for the conversation context."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from shared_kernel.db import metadata

workspaces = sa.Table(
    "workspaces",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "project_id", pg.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

chatrooms = sa.Table(
    "chatrooms",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "workspace_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("allow_org_members", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("allow_project_members", sa.Boolean, nullable=False, server_default=sa.text("true")),
    sa.Column("allow_project_owners_only", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("allow_guest_links", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("guest_token", sa.Text, nullable=False, unique=True),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

chatroom_agents = sa.Table(
    "chatroom_agents",
    metadata,
    sa.Column(
        "chatroom_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "agent_id", pg.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    ),
)

chatroom_guests = sa.Table(
    "chatroom_guests",
    metadata,
    sa.Column(
        "chatroom_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    ),
    sa.Column("joined_via_token", sa.Text, nullable=False),
    sa.Column("display_name", sa.String(100), nullable=True),
    sa.Column("joined_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)

messages = sa.Table(
    "messages",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "chatroom_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        # Must match the DB type created in migration 0017 (PG ENUM
        # `message_sender_type`), not sa.Text — asyncpg refuses to bind a
        # VARCHAR parameter into an enum column (DatatypeMismatchError).
        "sender_type",
        pg.ENUM("user", "agent", "system", name="message_sender_type", create_type=False),
        nullable=False,
    ),
    sa.Column("sender_id", pg.UUID(as_uuid=True), nullable=True),
    sa.Column("content_md", sa.Text, nullable=False, server_default=sa.text("''")),
    sa.Column("content_tsv", pg.TSVECTOR, nullable=True),
    sa.Column("metadata", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("edited_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

message_edits = sa.Table(
    "message_edits",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "message_id", pg.UUID(as_uuid=True), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("old_content_md", sa.Text, nullable=False),
    sa.Column(
        "edited_by_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("edited_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)

message_attachments = sa.Table(
    "message_attachments",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "message_id", pg.UUID(as_uuid=True), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=True
    ),
    sa.Column("filename", sa.Text, nullable=False),
    sa.Column("mime", sa.Text, nullable=False),
    sa.Column("size_bytes", sa.BigInteger, nullable=False),
    sa.Column("minio_path", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'active'")),
    sa.Column("scan_status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
    sa.Column("scan_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column(
        "chatroom_id", pg.UUID(as_uuid=True), sa.ForeignKey("chatrooms.id", ondelete="CASCADE"), nullable=True
    ),
    sa.Column(
        "uploaded_by_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
)


__all__ = [
    "chatroom_agents",
    "chatroom_guests",
    "chatrooms",
    "message_attachments",
    "message_edits",
    "messages",
    "workspaces",
]
