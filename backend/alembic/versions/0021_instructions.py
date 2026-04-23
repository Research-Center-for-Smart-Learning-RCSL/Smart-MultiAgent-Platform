"""Instruct chains — G.7 / §15.15–§15.17.

Tracks instruct messages between agents with loop detection via the
``path`` array and depth/count/wall-clock budget enforcement.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0021_instructions"
down_revision: str | Sequence[str] | None = "0020_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    instruction_state = pg.ENUM(
        "issued", "delivered", "completed", "rejected_loop", "timeout",
        name="instruction_state", create_type=False,
    )
    op.execute(
        "CREATE TYPE instruction_state AS ENUM "
        "('issued','delivered','completed','rejected_loop','timeout')"
    )

    op.create_table(
        "instructions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("chain_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("path", pg.ARRAY(pg.UUID(as_uuid=True)), nullable=False),
        sa.Column("depth", sa.Integer, nullable=False),
        sa.Column("issuer_agent_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("agents.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("target_agent_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("agents.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("state", instruction_state, nullable=False,
                  server_default=sa.text("'issued'::instruction_state")),
        sa.Column("issued_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_instructions_chain_id", "instructions", ["chain_id"])
    op.create_index(
        "ix_instructions_issuer_issued",
        "instructions",
        ["issuer_agent_id", sa.text("issued_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_instructions_issuer_issued", table_name="instructions")
    op.drop_index("ix_instructions_chain_id", table_name="instructions")
    op.drop_table("instructions")
    op.execute("DROP TYPE IF EXISTS instruction_state")
