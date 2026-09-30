"""Bump agents_context_cap_bounded to 1_050_000.

The catalogue now includes models with a 1 050 000-token context window
(previously the widest was 1 000 000). MAX_CONTEXT_TOKEN_CAP in
contexts/agents/domain/models.py derives from the widest window in the
catalogue, so the DB CHECK must track it.

Same NOT VALID + VALIDATE CONSTRAINT pattern as 0057 to avoid an
ACCESS EXCLUSIVE lock on the hot ``agents`` table.

Revision ID: 0097_bump_context_cap_bound
Revises: 0096_drop_audit_logs_actor_fk
"""

from __future__ import annotations

from alembic import op

revision = "0097_bump_context_cap_bound"
down_revision = "0096_drop_audit_logs_actor_fk"

_NEW_CAP = 1_050_000
_OLD_CAP = 1_000_000

_NEW_CHECK = f"context_token_cap IS NULL OR (context_token_cap > 0 AND context_token_cap <= {_NEW_CAP})"
_OLD_CHECK = f"context_token_cap IS NULL OR (context_token_cap > 0 AND context_token_cap <= {_OLD_CAP})"


def upgrade() -> None:
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_agents_agents_context_cap_bounded")
    op.execute(
        "ALTER TABLE agents ADD CONSTRAINT ck_agents_agents_context_cap_bounded "
        f"CHECK ({_NEW_CHECK}) NOT VALID"
    )
    op.execute("ALTER TABLE agents VALIDATE CONSTRAINT ck_agents_agents_context_cap_bounded")


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_agents_agents_context_cap_bounded")
    op.execute(
        "ALTER TABLE agents ADD CONSTRAINT ck_agents_agents_context_cap_bounded "
        f"CHECK ({_OLD_CHECK}) NOT VALID"
    )
    op.execute("ALTER TABLE agents VALIDATE CONSTRAINT ck_agents_agents_context_cap_bounded")
