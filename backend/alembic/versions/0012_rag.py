"""rag_configs + rag_documents + rag_chunks — E.5 / §10 / R10.01–R10.11.

RAG is configured per-Project (``rag_configs``), composed of one embedding
key (single `api_keys` row — not a Key Group — per R10.05) and an optional
rerank key. Documents are stored in MinIO (`minio_path`) with SHA-256-based
dedup semantics enforced by :class:`RagDocumentRepository.find_by_sha`
rather than a DB UNIQUE constraint, because the same SHA may legitimately
be uploaded into *different* configs (R10.11 project-scoping is enforced
by the service, not the index).

Chunks live in Postgres purely for traceability — the actual vectors live
in Qdrant under the ``rag_{project_id}`` collection; ``qdrant_point_id``
is the bridge.

Also installs the deferred FK from ``agents.rag_config_id`` → ``rag_configs``
that 0011 could not declare because the target table did not yet exist.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0012_rag"
down_revision: str | Sequence[str] | None = "0011_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CHUNK_STRATEGIES: tuple[str, ...] = ("fixed", "semantic")
_DOC_STATUSES: tuple[str, ...] = ("ingesting", "ready", "failed", "quarantined")
_SCAN_STATUSES: tuple[str, ...] = ("pending", "clean", "quarantined", "skipped")


def upgrade() -> None:
    op.execute(
        "CREATE TYPE rag_chunk_strategy AS ENUM ("
        + ", ".join(f"'{v}'" for v in _CHUNK_STRATEGIES)
        + ")"
    )
    op.execute(
        "CREATE TYPE rag_document_status AS ENUM ("
        + ", ".join(f"'{v}'" for v in _DOC_STATUSES)
        + ")"
    )
    op.execute(
        "CREATE TYPE rag_scan_status AS ENUM ("
        + ", ".join(f"'{v}'" for v in _SCAN_STATUSES)
        + ")"
    )

    op.create_table(
        "rag_configs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("chunk_strategy",
                  pg.ENUM(*_CHUNK_STRATEGIES,
                          name="rag_chunk_strategy", create_type=False),
                  nullable=False),
        sa.Column("chunk_params", pg.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        # Single key (not a group) per R10.05 — attach rejects keys that
        # lack the `embedding` capability at the application layer.
        sa.Column("embed_key_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("api_keys.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("embed_provider", sa.Text(), nullable=False),
        sa.Column("embed_model", sa.Text(), nullable=False),
        sa.Column("rerank_enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("rerank_key_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("api_keys.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("rerank_provider", sa.Text(), nullable=True),
        sa.Column("rerank_model", sa.Text(), nullable=True),
        sa.Column("top_k", sa.Integer(), nullable=False,
                  server_default=sa.text("8")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("top_k > 0 AND top_k <= 100", name="rag_top_k_bounds"),
    )
    op.create_index("ix_rag_configs_project", "rag_configs", ["project_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_rag_configs_project_name_active "
        "ON rag_configs (project_id, name) WHERE deleted_at IS NULL"
    )

    op.create_table(
        "rag_documents",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("rag_config_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("rag_configs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("minio_path", sa.Text(), nullable=False),
        sa.Column(
            "status",
            pg.ENUM(*_DOC_STATUSES, name="rag_document_status", create_type=False),
            nullable=False,
            server_default=sa.text("'ingesting'::rag_document_status"),
        ),
        sa.Column(
            "scan_status",
            pg.ENUM(*_SCAN_STATUSES, name="rag_scan_status", create_type=False),
            nullable=False,
            server_default=sa.text("'pending'::rag_scan_status"),
        ),
        sa.Column("scan_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("uploaded_by", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("uploaded_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_rag_documents_config", "rag_documents", ["rag_config_id"])
    op.create_index(
        "ix_rag_documents_config_sha",
        "rag_documents",
        ["rag_config_id", "sha256"],
    )

    op.create_table(
        "rag_chunks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("document_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("rag_documents.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("chunk_idx", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("qdrant_point_id", pg.UUID(as_uuid=True), nullable=False),
        sa.UniqueConstraint("document_id", "chunk_idx", name="uq_rag_chunk_doc_idx"),
    )
    op.create_index("ix_rag_chunks_document", "rag_chunks", ["document_id"])

    # Late-bind the FK the 0011 migration could not write.
    op.create_foreign_key(
        "fk_agents_rag_config",
        source_table="agents",
        referent_table="rag_configs",
        local_cols=["rag_config_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_agents_rag_config", "agents", type_="foreignkey")
    op.drop_index("ix_rag_chunks_document", table_name="rag_chunks")
    op.drop_table("rag_chunks")
    op.drop_index("ix_rag_documents_config_sha", table_name="rag_documents")
    op.drop_index("ix_rag_documents_config", table_name="rag_documents")
    op.drop_table("rag_documents")
    op.execute("DROP INDEX IF EXISTS uq_rag_configs_project_name_active")
    op.drop_index("ix_rag_configs_project", table_name="rag_configs")
    op.drop_table("rag_configs")
    op.execute("DROP TYPE rag_scan_status")
    op.execute("DROP TYPE rag_document_status")
    op.execute("DROP TYPE rag_chunk_strategy")
