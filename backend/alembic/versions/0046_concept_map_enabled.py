"""Concept Map privacy opt-in — strict-by-default enablement (Phase 2b WS3, R11.10/R11.17).

Adds ``concept_map_enabled BOOLEAN NOT NULL DEFAULT false`` to the wide-layer owner
rows (``agent_groups``, ``workspaces``). A wide (agent_group / workspace) Concept
Map contributes nothing to retrieval until a strict Project Owner opts it in; the
default-false server default makes every existing owner private on upgrade with no
backfill. Chatroom-owned maps inherit the room ACL and need no flag.

Expand-only and forward-compatible: old code ignores the column; the downgrade
drops it and every wide map degrades to disabled (its former effective state).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0046_concept_map_enabled"
down_revision: str | Sequence[str] | None = "0045_graphrag_embed_pin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("agent_groups", "workspaces"):
        op.add_column(
            table,
            sa.Column(
                "concept_map_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    for table in ("workspaces", "agent_groups"):
        op.drop_column(table, "concept_map_enabled")
