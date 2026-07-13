"""activity_types + activity_sessions + activity_submissions (Structured Activities core).

Chapter §30 — a generic bounded context for typed, schema-validated, server-scored
participant submissions within a room (R30.01-R30.11). Three tables:

- ``activity_types``     — a project-scoped registered type: payload JSON Schema + a
                           validator configuration (in_process | mcp | webhook).
- ``activity_sessions``  — a subject's run of a type in a room, carrying a server-assigned
                           monotonic ``attempt_no`` counter (numbered on submission).
- ``activity_submissions`` — the authoritative record of one scored submission.

Mints three new PG ENUMs (``validator_kind`` / ``validation_status`` / ``session_status``)
— unlike 0048 which reused existing types. The downgrade drops only these three tables and
these three ENUMs, in reverse dependency order.

Two partial-unique indexes encode invariants the service relies on:
- ``activity_types (project_id, key) WHERE deleted_at IS NULL`` — one live type per key.
- ``activity_sessions (activity_type_id, chatroom_id, subject_user_id) WHERE status='open'``
  — the lazy-open race guard (a concurrent second first-submission conflicts, §5.4).

D-1 (research retention): ``activity_types.retention_days`` + ``activity_submissions.retain_until``
let a project retain the authoritative research record past its room's 60-day post-soft-delete
purge (the retention worker skips a room while a child submission is still retained). The
``chatroom_id`` FK stays ``ON DELETE CASCADE``; retention is enforced by deferring the room purge.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0049_activities"
down_revision: str | Sequence[str] | None = "0048_knowmap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    validator_kind = pg.ENUM("in_process", "mcp", "webhook", name="validator_kind")
    validation_status = pg.ENUM("pending", "validated", "error", name="validation_status")
    session_status = pg.ENUM("open", "closed", name="session_status")
    bind = op.get_bind()
    validator_kind.create(bind, checkfirst=True)
    validation_status.create(bind, checkfirst=True)
    session_status.create(bind, checkfirst=True)

    op.create_table(
        "activity_types",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("payload_schema", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "validator_kind",
            pg.ENUM("in_process", "mcp", "webhook", name="validator_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("validator_config", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        # D-1 — NULL means "follow the room's normal purge"; a positive integer retains
        # submissions for that many days past creation (retain_until on each submission).
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_activity_types_project", "activity_types", ["project_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_activity_types_project_key_active "
        "ON activity_types (project_id, key) WHERE deleted_at IS NULL"
    )

    op.create_table(
        "activity_sessions",
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
        sa.Column(
            "status",
            pg.ENUM("open", "closed", name="session_status", create_type=False),
            nullable=False,
            server_default=sa.text("'open'::session_status"),
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_activity_sessions_chatroom", "activity_sessions", ["chatroom_id"])
    op.create_index("ix_activity_sessions_subject", "activity_sessions", ["subject_user_id"])
    # Lazy-open race guard (§5.4): at most one open session per (type, room, subject).
    op.execute(
        "CREATE UNIQUE INDEX uq_activity_sessions_open "
        "ON activity_sessions (activity_type_id, chatroom_id, subject_user_id) WHERE status = 'open'"
    )

    op.create_table(
        "activity_submissions",
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
        sa.Column("payload", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column(
            "validation_status",
            pg.ENUM("pending", "validated", "error", name="validation_status", create_type=False),
            nullable=False,
            server_default=sa.text("'pending'::validation_status"),
        ),
        sa.Column("is_valid", sa.Boolean(), nullable=True),
        sa.Column("error_class", sa.Text(), nullable=True),
        sa.Column("sub_scores", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        # D-1 — research retention horizon; NULL follows the room's normal purge.
        sa.Column("retain_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("validated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_activity_submissions_session", "activity_submissions", ["session_id"])
    op.create_index(
        "ix_activity_submissions_room_created", "activity_submissions", ["chatroom_id", "created_at"]
    )
    # Watchdog sweep of stale pending rows (validation_status, created_at).
    op.create_index(
        "ix_activity_submissions_status_created",
        "activity_submissions",
        ["validation_status", "created_at"],
    )
    # D-1 — backs the retention worker's "is any submission in this room still retained?"
    # EXISTS check; partial so only retained rows are indexed.
    op.execute(
        "CREATE INDEX ix_activity_submissions_retained "
        "ON activity_submissions (chatroom_id) WHERE retain_until IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_activity_submissions_retained")
    op.drop_index("ix_activity_submissions_status_created", table_name="activity_submissions")
    op.drop_index("ix_activity_submissions_room_created", table_name="activity_submissions")
    op.drop_index("ix_activity_submissions_session", table_name="activity_submissions")
    op.drop_table("activity_submissions")
    op.execute("DROP INDEX IF EXISTS uq_activity_sessions_open")
    op.drop_index("ix_activity_sessions_subject", table_name="activity_sessions")
    op.drop_index("ix_activity_sessions_chatroom", table_name="activity_sessions")
    op.drop_table("activity_sessions")
    op.execute("DROP INDEX IF EXISTS uq_activity_types_project_key_active")
    op.drop_index("ix_activity_types_project", table_name="activity_types")
    op.drop_table("activity_types")

    bind = op.get_bind()
    pg.ENUM(name="session_status").drop(bind, checkfirst=True)
    pg.ENUM(name="validation_status").drop(bind, checkfirst=True)
    pg.ENUM(name="validator_kind").drop(bind, checkfirst=True)
