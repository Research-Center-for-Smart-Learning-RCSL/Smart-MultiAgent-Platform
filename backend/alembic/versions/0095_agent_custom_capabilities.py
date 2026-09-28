"""Add custom_capabilities JSONB column to agents table.

Allows users to declare model capabilities for custom models not in the
catalog, overriding the conservative floor that disables all optional
parameters for unknown model IDs.

Revision ID: 0095_agent_custom_capabilities
Revises: 0094_canvas_templates
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0095_agent_custom_capabilities"
down_revision = "0094_canvas_templates"


def upgrade() -> None:
    op.add_column("agents", sa.Column("custom_capabilities", pg.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "custom_capabilities")
