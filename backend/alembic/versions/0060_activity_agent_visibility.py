"""activity_types.expose_payload_to_agent/echo_includes_content + activity_submissions.agent_digest.

Agent-visibility follow-up to Chapter §30. Adds two independent presentation gates
on ``activity_types`` (agent-facing context block vs. human-visible chat echo) and
one persisted text column on ``activity_submissions`` (the digest those two gates
control) — see ``contexts/activities/application/agent_digest.py``.

``expose_payload_to_agent`` defaults true (agents see submission content by
default); ``echo_includes_content`` defaults false (preserves today's outcome-only
echo unless an admin opts a type in for a public/collaborative activity).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0060_activity_agent_visibility"
down_revision: str | Sequence[str] | None = "0059_build_started_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "activity_types",
        sa.Column("expose_payload_to_agent", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "activity_types",
        sa.Column("echo_includes_content", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("activity_submissions", sa.Column("agent_digest", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("activity_submissions", "agent_digest")
    op.drop_column("activity_types", "echo_includes_content")
    op.drop_column("activity_types", "expose_payload_to_agent")
