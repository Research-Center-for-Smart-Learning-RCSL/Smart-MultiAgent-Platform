"""SQLAlchemy Core tables for the activities context (Chapter §30).

DDL is owned by ``alembic/versions/0049_activities.py``. This module exists so
the shared_kernel db registry can import the bindings; every column type
(especially the PG ENUMs, referenced with ``create_type=False``) matches that
migration verbatim (the ORM enum/type-match rule).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from shared_kernel.db import metadata

_validator_kind = pg.ENUM("in_process", "mcp", "webhook", name="validator_kind", create_type=False)
_validation_status = pg.ENUM("pending", "validated", "error", name="validation_status", create_type=False)
_session_status = pg.ENUM("open", "closed", name="session_status", create_type=False)
_activation_status = pg.ENUM("active", "ended", name="activation_status", create_type=False)

activity_types = sa.Table(
    "activity_types",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "project_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("key", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("payload_schema", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("validator_kind", _validator_kind, nullable=False),
    sa.Column("validator_config", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("retention_days", sa.Integer, nullable=True),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

activity_sessions = sa.Table(
    "activity_sessions",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "activity_type_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("activity_types.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "chatroom_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "subject_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("status", _session_status, nullable=False, server_default=sa.text("'open'::session_status")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

activity_activations = sa.Table(
    "activity_activations",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "chatroom_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "activity_type_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("activity_types.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "started_by_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "status",
        _activation_status,
        nullable=False,
        server_default=sa.text("'active'::activation_status"),
    ),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

activity_submissions = sa.Table(
    "activity_submissions",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "session_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("activity_sessions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "activity_type_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("activity_types.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "chatroom_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "producer_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("attempt_no", sa.Integer, nullable=False),
    sa.Column(
        "validation_status",
        _validation_status,
        nullable=False,
        server_default=sa.text("'pending'::validation_status"),
    ),
    sa.Column("is_valid", sa.Boolean, nullable=True),
    sa.Column("error_class", sa.Text, nullable=True),
    sa.Column("sub_scores", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("latency_ms", sa.Integer, nullable=True),
    sa.Column("retain_until", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("validated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)


__all__ = ["activity_activations", "activity_sessions", "activity_submissions", "activity_types"]
