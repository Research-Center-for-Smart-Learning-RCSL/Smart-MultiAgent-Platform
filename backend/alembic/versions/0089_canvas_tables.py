"""Add canvas tables: canvases, canvas_objects, canvas_snapshots.

New bounded context for the collaborative canvas feature ([R13.42]-[R13.50]).
Also adds may_read_canvas grant column to chatroom_agents.

Reversible: DROP TABLE cascade and DROP TYPE.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0089_canvas_tables"
down_revision: str | Sequence[str] | None = "0088_encrypt_proxy_headers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PG ENUM for canvas object kind
    canvas_object_kind = pg.ENUM(
        "note", "text", "image", "shape", "drawing", "connector",
        name="canvas_object_kind",
        create_type=False,
    )
    canvas_object_kind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "canvases",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "chatroom_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
            nullable=False, unique=True,
        ),
        sa.Column("expose_to_agents", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("crdt_state", sa.LargeBinary, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_table(
        "canvas_objects",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "canvas_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("canvases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "kind",
            pg.ENUM("note", "text", "image", "shape", "drawing", "connector",
                    name="canvas_object_kind", create_type=False),
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
            "created_by_user_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_guest_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("guest_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_canvas_objects_canvas_id", "canvas_objects", ["canvas_id"])

    op.create_table(
        "canvas_snapshots",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "canvas_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("canvases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_data", pg.JSONB, nullable=False),
        sa.Column("agent_digest", sa.Text, nullable=True),
        sa.Column(
            "created_by_user_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_canvas_snapshots_canvas_id", "canvas_snapshots", ["canvas_id"])

    # Agent grant column on chatroom_agents
    op.add_column(
        "chatroom_agents",
        sa.Column("may_read_canvas", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("chatroom_agents", "may_read_canvas")
    op.drop_table("canvas_snapshots")
    op.drop_table("canvas_objects")
    op.drop_table("canvases")
    op.execute("DROP TYPE IF EXISTS canvas_object_kind")
