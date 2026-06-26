"""Per-agent RAG document scoping — agent allowlist on rag_documents.

A RAG config is project-scoped and shared by every agent bound to it
(``agents.rag_config_id``). This adds a strict per-document allowlist:
``rag_documents.agent_ids`` enumerates exactly which agents may retrieve a
document's chunks. An empty array means *no* agent can see it (the value is a
positive allowlist, not a deny-list).

Retrieval enforces the allowlist in ``RetrieveService.query``: it resolves the
querying agent's visible documents via ``RagDocumentRepository.allowed_document_ids``
(``agent_ids @> [agent_id]`` — served by the GIN index below) and passes them to
Qdrant as a ``doc_id`` filter, so the vector top_k is computed over allowed docs.
This keeps the access-control out of the vector-store payload, so existing Qdrant
points need no payload rewrite — backfilling this column is sufficient for the
feature to take effect.

Backfill preserves today's behaviour: every existing document becomes visible
to all agents currently bound to its config, so no corpus silently disappears
on upgrade.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0035_rag_document_agent_scope"
down_revision: str | Sequence[str] | None = "0034_messages_sender_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rag_documents",
        sa.Column(
            "agent_ids",
            pg.ARRAY(pg.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
    )
    # Backfill: existing docs stay visible to every agent currently bound to
    # their config, so the upgrade is behaviour-preserving.
    op.execute(
        """
        UPDATE rag_documents d
        SET agent_ids = COALESCE(
            (SELECT array_agg(a.id) FROM agents a WHERE a.rag_config_id = d.rag_config_id),
            '{}'::uuid[]
        )
        """
    )
    # GIN index keeps the `:agent_id = ANY(agent_ids)` retrieval filter cheap.
    op.create_index(
        "ix_rag_documents_agent_ids",
        "rag_documents",
        ["agent_ids"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_rag_documents_agent_ids", table_name="rag_documents")
    op.drop_column("rag_documents", "agent_ids")
