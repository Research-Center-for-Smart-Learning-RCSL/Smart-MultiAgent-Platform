"""Enforce document identity and add ingest ownership metadata.

The upload services deduplicate by ``(config_id, sha256)`` and recover an insert
race by catching a uniqueness failure, but the original schema created only
non-unique lookup indexes. Concurrent first uploads could therefore persist two
documents and the recovery branch could not run against PostgreSQL.

The migration deliberately refuses to guess how duplicate rows should be merged:
their allowlists may differ and automatically unioning them would widen access.
Operators get an actionable exception before any schema change. Once the data is
clean, the non-unique indexes are replaced by named unique constraints.

Nullable claim metadata is expand-only support for rolling out attempt ownership.
Existing jobs remain readable and are adopted through the legacy worker path until
all producers pass a claim token.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0069_knowledge_ingest_ownership"
down_revision: str | Sequence[str] | None = "0068_approval_chatroom_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DUPLICATE_PREFLIGHT = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM {table}
        GROUP BY {config_column}, sha256
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION
            '{table} contains duplicate ({config_column}, sha256) groups'
            USING HINT =
                'Run the duplicate inventory query from '
                'docs/tasks/2026-07-29-knowledge-ingest-concurrency-and-enqueue/spec.md '
                'and resolve each group without widening agent_ids.';
    END IF;
END
$$
"""


def _upgrade_documents(
    *,
    table: str,
    config_column: str,
    old_index: str,
    unique_name: str,
) -> None:
    op.execute(
        _DUPLICATE_PREFLIGHT.format(
            table=table,
            config_column=config_column,
        )
    )
    op.drop_index(old_index, table_name=table)
    op.create_unique_constraint(unique_name, table, [config_column, "sha256"])
    op.add_column(table, sa.Column("ingest_claim_token", pg.UUID(as_uuid=True), nullable=True))
    op.add_column(
        table,
        sa.Column("ingest_claim_until", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def upgrade() -> None:
    _upgrade_documents(
        table="rag_documents",
        config_column="rag_config_id",
        old_index="ix_rag_documents_config_sha",
        unique_name="uq_rag_documents_config_sha",
    )
    _upgrade_documents(
        table="knowmap_documents",
        config_column="knowmap_config_id",
        old_index="ix_knowmap_documents_config_sha",
        unique_name="uq_knowmap_documents_config_sha",
    )


def _downgrade_documents(
    *,
    table: str,
    config_column: str,
    old_index: str,
    unique_name: str,
) -> None:
    op.drop_column(table, "ingest_claim_until")
    op.drop_column(table, "ingest_claim_token")
    op.drop_constraint(unique_name, table, type_="unique")
    op.create_index(old_index, table, [config_column, "sha256"])


def downgrade() -> None:
    _downgrade_documents(
        table="knowmap_documents",
        config_column="knowmap_config_id",
        old_index="ix_knowmap_documents_config_sha",
        unique_name="uq_knowmap_documents_config_sha",
    )
    _downgrade_documents(
        table="rag_documents",
        config_column="rag_config_id",
        old_index="ix_rag_documents_config_sha",
        unique_name="uq_rag_documents_config_sha",
    )

