"""Add model_id to agents — per-agent model selection (P16).

Revision ID: 0030_agent_model_id
Revises: 0029_version_bump_triggers

Adds a nullable TEXT column so users can pick a concrete model (e.g.
``claude-sonnet-4-6``, ``gpt-4o-mini``) rather than relying on the
provider-level ``model_hint`` default.  NULL means "use the platform
default for the provider".
"""

revision = "0030_agent_model_id"
down_revision = "0029_version_bump_triggers"

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column("agents", sa.Column("model_id", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "model_id")
