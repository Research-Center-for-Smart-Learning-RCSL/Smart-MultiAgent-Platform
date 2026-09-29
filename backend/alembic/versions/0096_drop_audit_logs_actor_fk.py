"""Drop foreign key on audit_logs.actor_user_id.

The audit_logs table records actions by regular users, system actors
(UUID(int=0) for agent self-modification), and guest users (ephemeral
UUIDs not present in the users table).  The FK constraint
fk_audit_logs_actor_user_id_users rejects writes for the latter two,
crashing agent turns and guest session creation.

The column stays nullable; only the FK is removed.

Revision ID: 0096_drop_audit_logs_actor_fk
Revises: 0095_agent_custom_capabilities
"""

from __future__ import annotations

from alembic import op

revision = "0096_drop_audit_logs_actor_fk"
down_revision = "0095_agent_custom_capabilities"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS fk_audit_logs_actor_user_id_users"
    )


def downgrade() -> None:
    op.create_foreign_key(
        "fk_audit_logs_actor_user_id_users",
        "audit_logs",
        "users",
        ["actor_user_id"],
        ["id"],
    )
