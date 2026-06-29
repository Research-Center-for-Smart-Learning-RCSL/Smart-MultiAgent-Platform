"""Add per-agent reasoning-effort level.

Adds a nullable ``effort`` column (enum ``agent_effort``: low/medium/high) to
``agents``. NULL means "not set" — the turn engine omits the provider effort
parameter and each provider's own default applies. Mapped per-provider at the
adapter boundary (Claude ``output_config.effort`` / OpenAI ``reasoning_effort``
/ Gemini ``thinkingConfig.thinkingLevel``).

Revision ID: 0039_agent_effort
Revises: 0038_agent_workspace_files
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0039_agent_effort"
down_revision: str = "0038_agent_workspace_files"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_effort = pg.ENUM("low", "medium", "high", name="agent_effort", create_type=False)


def upgrade() -> None:
    _effort.create(op.get_bind(), checkfirst=True)
    op.add_column("agents", sa.Column("effort", _effort, nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "effort")
    _effort.drop(op.get_bind(), checkfirst=True)
