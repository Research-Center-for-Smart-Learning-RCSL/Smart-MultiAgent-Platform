"""chatrooms + chatroom_agents + chatroom_guests — F.2 / §21.1.

Four independent boolean access flags per R13.04. `guest_token` is UNIQUE
(CSPRNG 32-byte base64url ≈ 192 bits); tokens are non-secret room-scoped
identifiers (R13.05 / R13.07). Soft-delete + optimistic-lock `version` via
the existing `smap_bump_version` trigger from 0002_tenancy.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0016_chatrooms"
down_revision: str | Sequence[str] | None = "0015_workspaces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chatrooms",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("allow_org_members", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("allow_project_members", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("allow_project_owners_only", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("allow_guest_links", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("guest_token", sa.Text(), nullable=False, unique=True),
        sa.Column("version", sa.Integer(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_chatrooms_workspace",
        "chatrooms",
        ["workspace_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # Re-use the generic version-bump trigger defined in 0002_tenancy so
    # the If-Match contract is consistent across resources.
    op.execute(
        "CREATE TRIGGER trg_chatrooms_bump_version "
        "BEFORE UPDATE ON chatrooms FOR EACH ROW "
        "EXECUTE FUNCTION smap_bump_version();"
    )

    op.create_table(
        "chatroom_agents",
        sa.Column("chatroom_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("agent_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("agents.id", ondelete="CASCADE"),
                  nullable=False),
        sa.PrimaryKeyConstraint(
            "chatroom_id", "agent_id", name="pk_chatroom_agents",
        ),
    )

    op.create_table(
        "chatroom_guests",
        sa.Column("chatroom_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("joined_via_token", sa.Text(), nullable=False),
        sa.Column("joined_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint(
            "chatroom_id", "user_id", name="pk_chatroom_guests",
        ),
    )


def downgrade() -> None:
    op.drop_table("chatroom_guests")
    op.drop_table("chatroom_agents")
    op.execute("DROP TRIGGER IF EXISTS trg_chatrooms_bump_version ON chatrooms;")
    op.drop_index("ix_chatrooms_workspace", table_name="chatrooms")
    op.drop_table("chatrooms")
