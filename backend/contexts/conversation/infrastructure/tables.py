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
    # Phase 2b WS3 (R11.10/R11.17, migration 0046): strict-by-default privacy
    # opt-in for a workspace-owned Concept Map — disabled until a Project Owner
    # enables it. Bool NOT NULL, matching the migration server_default false.
    sa.Column(
        "concept_map_enabled",
        sa.Boolean,
        nullable=False,
        server_default=sa.text("false"),
    ),
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
    sa.Column(
        "created_by_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("disclose_observers", sa.Boolean, nullable=False, server_default=sa.text("true")),
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
    # PG ENUM created in migration 0041 — must match the DB type, not sa.Text
    # (see the sender_type comment below for the asyncpg constraint).
    sa.Column(
        "role",
        pg.ENUM("normal", "observer", name="chatroom_agent_role", create_type=False),
        nullable=False,
        server_default=sa.text("'normal'::chatroom_agent_role"),
    ),
    # Delegated activity control ([R30.37], migration 0078). Two CHECK constraints
    # in the DB keep a live grant from being empty or unattributable; they are not
    # declared here because SQLAlchemy Core would then try to create them, and the
    # migration owns the schema. `pg.JSONB`/`sa.Boolean` match 0078 exactly, per the
    # ORM/migration type rule above.
    sa.Column(
        "may_control_activities",
        sa.Boolean,
        nullable=False,
        server_default=sa.text("false"),
    ),
    sa.Column(
        "activity_type_allowlist",
        pg.JSONB,
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    ),
    sa.Column(
        "granted_by_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
)

agent_observations = sa.Table(
    "agent_observations",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "chatroom_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "agent_id", pg.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("content_md", sa.Text, nullable=False),
    sa.Column("metadata", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("trigger", sa.Text, nullable=False),
    sa.Column("trigger_message_id", pg.UUID(as_uuid=True), nullable=True),
    sa.Column("released_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("release_target", pg.JSONB, nullable=True),
    sa.Column(
        "released_by_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
    # PG ENUMs created in migration 0017 — must match the DB type, not sa.Text,
    # or asyncpg binds a VARCHAR param into the enum column and every WHERE/SET
    # on these columns fails (operator does not exist: <enum> = character varying).
    # Same fix as sender_type above.
    sa.Column(
        "status",
        pg.ENUM("active", "quarantined", "expired", name="message_attachment_status", create_type=False),
        nullable=False,
        server_default=sa.text("'active'::message_attachment_status"),
    ),
    sa.Column(
        "scan_status",
        pg.ENUM("pending", "clean", "quarantined", "skipped", name="message_scan_status", create_type=False),
        nullable=False,
        server_default=sa.text("'pending'::message_scan_status"),
    ),
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
    sa.Column("extracted_text", sa.Text, nullable=True),
    # PG ENUM created in migration 0040 — must match the DB type, not sa.Text
    # (see the sender_type/status comments above for the asyncpg constraint).
    sa.Column(
        "extraction_status",
        pg.ENUM(
            "pending",
            "extracted",
            "empty",
            "unsupported",
            "failed",
            name="message_attachment_extraction_status",
            create_type=False,
        ),
        nullable=False,
        server_default=sa.text("'pending'::message_attachment_extraction_status"),
    ),
    sa.Column("extracted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)


__all__ = [
    "agent_observations",
    "chatroom_agents",
    "chatroom_guests",
    "chatrooms",
    "message_attachments",
    "message_edits",
    "messages",
    "workspaces",
]
