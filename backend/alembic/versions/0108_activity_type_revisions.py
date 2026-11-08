"""Activity type revisions and the version a submission was made under.

Spec: docs/tasks/2026-11-08-research-data-integrity/spec.md, section 5
(Versioning), Q-2 and Q-7. The spec's single 0108 is split: the judging columns
arrive in 0109 with milestone 4 (D-13), so each milestone reverts on its own.

Old code runs on this schema unchanged: ``type_version`` is nullable and nothing
pre-0108 writes or reads it. A type created by old code during a rolling deploy
has no revision row until its next behavioural edit, which the reader of a
revision must tolerate anyway for the same reason ``type_version`` may be NULL.

The backfill fills ``type_version`` only where it is provable (Q-7): a type
still at version 1 was never bumped, so all its submissions are version 1.
Every other existing submission stays NULL and exports as ``unknown``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0108_activity_type_revisions"
down_revision: str = "0107_class_closed_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "activity_type_revisions"
_VERSION_CHECK = "ck_activity_type_revisions_version"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "activity_type_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("activity_types.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("payload_schema", pg.JSONB, nullable=False),
        sa.Column(
            "validator_kind",
            pg.ENUM("in_process", "mcp", "webhook", name="validator_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("validator_config", pg.JSONB, nullable=False),
        sa.Column("group_config", pg.JSONB, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column(
            "created_by",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint("version >= 1", name=_VERSION_CHECK),
    )
    # Seeded rows take now() rather than the type's created_at: when a version
    # above 1 came into being is not recorded anywhere, and created_by is NULL
    # for the same reason.
    op.execute(
        f"INSERT INTO {_TABLE} "
        "(activity_type_id, version, payload_schema, validator_kind, validator_config, group_config) "
        "SELECT id, version, payload_schema, validator_kind, validator_config, group_config "
        "FROM activity_types"
    )

    op.add_column("activity_submissions", sa.Column("type_version", sa.Integer, nullable=True))
    op.execute(
        "UPDATE activity_submissions AS s SET type_version = 1 "
        "FROM activity_types AS t WHERE s.activity_type_id = t.id AND t.version = 1"
    )


def downgrade() -> None:
    op.drop_column("activity_submissions", "type_version")
    op.drop_table(_TABLE)
