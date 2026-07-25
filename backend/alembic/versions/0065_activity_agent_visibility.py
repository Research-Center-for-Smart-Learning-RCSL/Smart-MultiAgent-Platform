"""activity_types.expose_payload_to_agent/echo_includes_content + activity_submissions.agent_digest.

Agent-visibility follow-up to Chapter §30. Adds two presentation gates on
``activity_types`` and one persisted text column on ``activity_submissions``
(the digest those two gates control) — see
``contexts/activities/application/agent_digest.py``.

``expose_payload_to_agent`` is the master switch: when false, no channel ever
shows a submission's content. ``echo_includes_content`` is a secondary switch
that, only when the master is also on, additionally puts the content in the
room's human-visible chat echo (in addition to the agent-only context block) —
a chat message cannot be shown to humans without also being visible to any
agent reading that same transcript, so the two are not independent (see
``SubmissionService._echo_text`` call site in submission_service.py).

``expose_payload_to_agent`` defaults true (agents see submission content by
default); ``echo_includes_content`` defaults false (preserves the original
outcome-only echo unless an admin opts a type in for a public/collaborative
activity).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0065_activity_agent_visibility"
down_revision: str | Sequence[str] | None = "0064_egress_allowlist_seed_backfill"
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
