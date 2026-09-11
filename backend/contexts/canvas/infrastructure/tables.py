"""SQLAlchemy Core tables for the canvas context."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.dialects.postgresql import TSVECTOR

from shared_kernel.db import metadata

canvases = sa.Table(
    "canvases",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "chatroom_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    sa.Column("expose_to_agents", sa.Boolean, nullable=False, server_default=sa.text("true")),
    sa.Column("crdt_state", sa.LargeBinary, nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

canvas_objects = sa.Table(
    "canvas_objects",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "canvas_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("canvases.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "kind",
        pg.ENUM(
            "note",
            "text",
            "image",
            "shape",
            "drawing",
            "connector",
            name="canvas_object_kind",
            create_type=False,
        ),
        nullable=False,
    ),
    sa.Column("content", sa.Text, nullable=True),
    sa.Column("minio_path", sa.Text, nullable=True),
    sa.Column("position_x", sa.Float, nullable=False),
    sa.Column("position_y", sa.Float, nullable=False),
    sa.Column("width", sa.Float, nullable=False),
    sa.Column("height", sa.Float, nullable=False),
    sa.Column("z_index", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("style", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column(
        "created_by_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "created_by_guest_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("guest_sessions.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "created_by_agent_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("content_tsv", TSVECTOR, nullable=True),
)

canvas_object_comments = sa.Table(
    "canvas_object_comments",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "canvas_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("canvases.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "object_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("canvas_objects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column(
        "created_by_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "created_by_guest_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("guest_sessions.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

canvas_snapshots = sa.Table(
    "canvas_snapshots",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "canvas_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("canvases.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("snapshot_data", pg.JSONB, nullable=False),
    sa.Column("agent_digest", sa.Text, nullable=True),
    sa.Column(
        "created_by_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("label", sa.String(200), nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)
